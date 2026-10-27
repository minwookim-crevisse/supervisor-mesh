"""Behaviour scenarios against stub projmux / tmux / claude, one per review finding (Y1-Y20) plus the earlier rules
(D2, F1, F3, the --allow guard, exit 21). Run against an older smv.py (SMV_UNDER_TEST) to see the red result."""
import datetime as dt
import json
import os
import subprocess
import unittest

from harness import CHECKOUT, PLUGIN_ID, RULE, SKILL, TESTS, Env, ready_screen, uid
from test_screen import PERMISSION

INSTALL = f"/plugin install {PLUGIN_ID}"
T0 = dt.datetime(2026, 10, 22, 1, 0, 0, tzinfo=dt.timezone.utc)


def ts(i: int) -> str:
    return (T0 + dt.timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def out(r) -> str:
    return r.stdout + r.stderr


class Cleanup(unittest.TestCase):
    def install_row(self, e: Env) -> None:
        e.state["plugin_rows"] = [{"id": PLUGIN_ID, "scope": "project", "projectPath": str(e.proj), "version": "x"}]
        e.save()

    def test_y01_failed_uninstall_keeps_proj_and_rerun_retries(self):
        e = Env(self.addCleanup)
        e.make_run()
        self.install_row(e)
        e.state["fail"]["uninstall"] = 1
        e.save()
        r = e.smv("cleanup")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertTrue(e.proj.is_dir(), "a failed cleanup keeps the scratch project (and its digest key)")
        self.assertNotIn("cleaned", e.run_json())
        self.assertIn("LEFT project-scope install row", out(r))
        r = e.smv("cleanup")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertEqual(e.load()["plugin_rows"], [], "the rerun retried the uninstall")
        self.assertFalse(e.proj.exists())

    def test_y02_failed_delete_plan_blocks_every_mutation(self):
        e = Env(self.addCleanup)
        pm = e.make_run()
        e.state["fail"]["delete-plan"] = 1
        e.save()
        r = e.smv("cleanup")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertEqual([a for a in e.calls("projmux", "delete") if "--yes" in a], [], "no --yes after a failed plan")
        self.assertEqual(e.calls("projmux", "stop", "project"), [])
        self.assertNotIn(pm["metadata"]["uid"], e.load()["deleted"])

    def test_y02_foreign_agent_in_plan_stops_at_once(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.state["delete_plan_extra"] = f"\n  cascade agent/x uid={uid('agent')}"
        e.save()
        r = e.smv("cleanup")
        self.assertEqual(r.returncode, 4, out(r))
        self.assertEqual(e.calls("projmux", "stop", "project"), [])

    def test_y02_failed_unregister_plan_is_not_followed_by_yes(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.state["fail"]["unregister-plan"] = 1
        e.save()
        r = e.smv("cleanup")
        self.assertNotEqual(r.returncode, 0, out(r))
        self.assertEqual([a for a in e.calls("projmux", "unregister") if "--yes" in a], [])
        self.assertTrue(e.proj.is_dir())

    def test_y02_failed_stop_project_is_not_followed_by_unregister(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.state["fail"]["stop"] = 1
        e.save()
        r = e.smv("cleanup")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertEqual(e.calls("projmux", "unregister"), [])
        self.assertTrue(e.proj.is_dir())

    def test_y02_agent_appearing_during_cleanup_stops_before_stop_project(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.state["spawn_after_delete"] = e.agent("intruder", creator=uid("agent"))
        e.save()
        r = e.smv("cleanup")
        self.assertEqual(r.returncode, 4, out(r))
        self.assertEqual(e.calls("projmux", "stop", "project"), [])

    def test_y15_no_baseline_is_a_fail_line_not_a_crash(self):
        e = Env(self.addCleanup)
        e.make_run(baseline=False)
        r = e.smv("cleanup")
        self.assertNotIn("Traceback", out(r))
        self.assertIn("isolation not checked", out(r))
        self.assertEqual(r.returncode, 1)

    def test_f3_unrecorded_agent_stops_cleanup_with_4(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.state["agents"].append(e.agent("stranger", creator=uid("agent")))
        e.save()
        r = e.smv("cleanup")
        self.assertEqual(r.returncode, 4, out(r))
        self.assertEqual(e.calls("projmux", "delete"), [])

    def test_f1_process_left_in_proj_stops_with_4_and_is_not_killed(self):
        e = Env(self.addCleanup)
        e.make_run()
        p = subprocess.Popen(["sleep", "60"], cwd=e.proj)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        r = e.smv("cleanup", timeout=60)
        self.assertEqual(r.returncode, 4, out(r))
        self.assertIsNone(p.poll(), "smv never signals a process it did not record")
        self.assertTrue(e.proj.is_dir())


class ReadOnly(unittest.TestCase):
    def test_doctor_status_agents_and_cleanup_dry_run_change_nothing(self):
        e = Env(self.addCleanup)
        e.make_run(screen=(TESTS / "fixtures/ready-auto-mode.txt").read_text())
        r = e.smv("doctor", "--pre")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertIn("WARN auto mode on", out(r))
        self.assertEqual(e.smv("status").returncode, 0)
        self.assertEqual(e.smv("agents", "--expect-count", "1").returncode, 0)
        r = e.smv("cleanup", "--dry-run")
        self.assertEqual(r.returncode, 0, out(r))
        mutations = [a for a in e.calls("projmux") if "--yes" in a or a[:2] == ["stop", "project"]]
        self.assertEqual(mutations, [])
        self.assertTrue(e.proj.is_dir())
        self.assertEqual(e.keys_sent(), [])


class Slack(unittest.TestCase):
    def test_fake_slack_start_say_summary_stop(self):
        e = Env(self.addCleanup)
        e.make_run(lead=CHECKOUT)  # FakeSlack is imported read-only from the checkout's tests/test_slack.py
        r = e.smv("fake-slack", "start", timeout=60)
        self.addCleanup(e.smv, "fake-slack", "stop")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertEqual(e.smv("fake-slack", "say", "--text", "SMV-PING-0").returncode, 0)
        r = e.smv("fake-slack", "summary")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertEqual(json.loads(r.stdout)["messages"], 1)
        r = e.smv("fake-slack", "stop")
        self.assertIn("stopped pid", out(r))
        pids = e.run_json()["pids"]["fake-slack"]
        self.assertTrue(pids.get("port") and pids.get("stopped"), pids)


class Send(unittest.TestCase):
    def test_y03_slow_tui_never_gets_a_second_enter_on_a_plugin_line(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.pane()["on_enter"] = ["ignore"]
        e.save()
        r = e.smv("send", "--text", INSTALL)
        self.assertEqual(r.returncode, 3, out(r))
        self.assertEqual(sum(k == ["Enter"] for k in e.keys_sent()), 1)
        log = (e.run_root / "evidence/sends.log").read_text()
        self.assertIn("KEY Enter", log)

    def test_y03_approval_required_status_is_refused(self):
        e = Env(self.addCleanup)
        pm = e.make_run()
        e.state["status"][pm["metadata"]["uid"]] = "approval_required observedAt=2026-10-22T01:00:00Z lifecycle=x"
        e.save()
        r = e.smv("send", "--text", "hello")
        self.assertEqual(r.returncode, 11, out(r))
        self.assertEqual(e.keys_sent(), [])

    def test_y03_no_second_enter_into_a_dialog_under_an_old_box(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.pane()["screens"]["dialog"] = "\n".join(["output", RULE, "❯ {box}", RULE, "", *PERMISSION])
        e.pane()["on_enter"] = ["screen:dialog"]
        e.save()
        e.smv("send", "--text", "/supervisor-mesh:init python")
        self.assertEqual(sum(k == ["Enter"] for k in e.keys_sent()), 1)

    def test_y03_second_enter_only_after_the_same_check(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.pane()["on_enter"] = ["ignore", "submit"]
        e.save()
        r = e.smv("send", "--text", "/supervisor-mesh:init python")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertEqual(sum(k == ["Enter"] for k in e.keys_sent()), 2)
        log = (e.run_root / "evidence/sends.log").read_text()
        self.assertEqual(log.count("KEY Enter"), 2, log)


class Wait(unittest.TestCase):
    def send_record(self, e: Env, pm: dict, **extra) -> None:
        data = e.run_json()
        data["sends"][pm["metadata"]["uid"]] = {"at": ts(0), "text": "x", "capture": "", "pre": None, **extra}
        data["last_send"] = ts(0)
        (e.run_root / "evidence/run.json").write_text(json.dumps(data, indent=1))

    def test_y05_expect_never_beats_a_stop_or_logs_a_false_answer(self):
        e = Env(self.addCleanup)
        pm = e.make_run(screen="\n".join(["EXPECTED-TEXT appears in the command", *PERMISSION]))
        self.send_record(e, pm)
        data = e.run_json()
        data["pending_stops"] = {pm["metadata"]["uid"]: {"kind": "trust", "code": 10, "at": ts(0), "capture": "-"}}
        (e.run_root / "evidence/run.json").write_text(json.dumps(data, indent=1))
        r = e.smv("wait", "--expect", "EXPECTED-TEXT", "--timeout", "5", "--poll", "0.2")
        self.assertEqual(r.returncode, 11, out(r))
        self.assertFalse(e.run_json().get("operator_answers"))

    def test_y06_dash_observed_at_falls_back_to_the_screen(self):
        e = Env(self.addCleanup)
        pm = e.make_run()
        e.state["status"][pm["metadata"]["uid"]] = "idle observedAt=- lifecycle=- source=-"
        e.pane()["screens"]["after"] = ready_screen("⏺ done")
        e.pane()["on_enter"] = ["submit-screen:after"]
        e.save()
        self.assertEqual(e.smv("send", "--text", "hello").returncode, 0)
        r = e.smv("wait", "--timeout", "8", "--poll", "0.2")
        self.assertEqual(r.returncode, 0, out(r))

    def test_y06_unchanged_raw_screen_is_not_done(self):
        e = Env(self.addCleanup)
        e.make_run(screen=ready_screen(f"  contact {e.email}"))
        e.pane()["on_enter"] = ["submit"]
        e.save()
        self.assertEqual(e.smv("send", "--text", "hello").returncode, 0)
        r = e.smv("wait", "--timeout", "4", "--poll", "0.2")
        self.assertEqual(r.returncode, 124, out(r))


class Init(unittest.TestCase):
    def transcript(self, e: Env, records: list) -> None:
        path = e.base / "pm.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        pm = e.state["agents"][0]
        pm["status"]["sessionRef"]["claude"]["transcriptPath"] = str(path)
        e.save()
        data = e.run_json()
        data["last_send"] = ts(0)
        (e.run_root / "evidence/run.json").write_text(json.dumps(data, indent=1))

    @staticmethod
    def call(i: int, cmd: str) -> dict:
        return {"type": "assistant", "timestamp": ts(i), "message": {"content": [
            {"type": "tool_use", "id": f"toolu_{i}", "name": "Bash", "input": {"command": cmd}}]}}

    @staticmethod
    def result(i: int, call_i: int, body: str) -> dict:
        return {"type": "user", "timestamp": ts(i), "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"toolu_{call_i}", "content": body}]}}

    @staticmethod
    def echo(i: int, text: str) -> dict:
        return {"type": "assistant", "timestamp": ts(i), "message": {"content": [{"type": "text", "text": text}]}}

    def hashes(self, e: Env, files: list[str]) -> None:
        self.assertEqual(e.smv("hash", "--label", "before").returncode, 0)
        for rel in files:
            (e.proj / rel).parent.mkdir(parents=True, exist_ok=True)
            (e.proj / rel).write_text("x\n")
        self.assertEqual(e.smv("hash", "--label", "after").returncode, 0)

    def scaffold_out(self, dry: bool) -> str:
        return ("CREATE  AGENTS.md\nCREATE  docs/x.md\n# summary: created=2 merged=0 skipped=0\n"
                + ("" if not dry else "# dry-run\n"))

    def test_y04_dry_run_and_echo_are_not_proof(self):
        e = Env(self.addCleanup)
        e.make_run()
        script = f'python3 "{e.lead}/skills/init/scaffold.py" --target . --stack none'
        self.transcript(e, [self.call(1, script + " --dry-run"), self.result(2, 1, self.scaffold_out(True)),
                            self.echo(3, "# summary: created=2 merged=0 skipped=0")])
        self.hashes(e, ["AGENTS.md", "docs/x.md"])
        r = e.smv("init-check", "fresh", "--before", "before", "--after", "after")
        self.assertEqual(r.returncode, 1, out(r))

    def test_y04_echo_only_rerun_is_not_proof(self):
        e = Env(self.addCleanup)
        e.make_run()
        self.transcript(e, [self.echo(1, "# summary: created=0 merged=0 skipped=12")])
        self.hashes(e, [])
        r = e.smv("init-check", "rerun", "--before", "before", "--after", "after")
        self.assertEqual(r.returncode, 1, out(r))

    def test_y04_apply_is_never_credited_with_another_calls_result(self):
        """An apply call whose own result is missing or is_error fails, even when a dry-run result has a summary."""
        failed = self.result(4, 3, self.scaffold_out(False))
        failed["message"]["content"][0]["is_error"] = True
        for name, own in (("no result of its own", []), ("is_error result", [failed])):
            with self.subTest(name):
                e = Env(self.addCleanup)
                e.make_run()
                script = f'python3 "{e.lead}/skills/init/scaffold.py" --target . --stack none'
                self.transcript(e, [self.call(1, script + " --dry-run"), self.result(2, 1, self.scaffold_out(True)),
                                    self.call(3, script), *own])
                self.hashes(e, ["AGENTS.md", "docs/x.md"])
                r = e.smv("init-check", "fresh", "--before", "before", "--after", "after")
                self.assertEqual(r.returncode, 1, out(r))
                self.assertIn("FAIL  an apply run", out(r))

    RERUN_OUT = "SKIP    AGENTS.md\nSKIP    docs/x.md\n# summary: created=0 merged=0 skipped=14\n"

    def mixed(self, e: Env, outputs: list[str]) -> list[dict]:
        """The live re-run shape: preview and apply of scaffold.py in ONE Bash call, both outputs in its result."""
        script = f'python3 "{e.lead}/skills/init/scaffold.py" --target "{e.proj}" --stack python'
        return [self.call(1, f"{script} --dry-run && {script}"), self.result(2, 1, "".join(outputs))]

    def test_y04_rerun_mixed_call_with_zero_summaries_and_no_change_passes(self):
        e = Env(self.addCleanup)
        e.make_run()
        self.transcript(e, self.mixed(e, [self.RERUN_OUT, self.RERUN_OUT]))
        self.hashes(e, [])
        r = e.smv("init-check", "rerun", "--before", "before", "--after", "after")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertIn("mixed call toolu_1: every summary created=0 merged=0 (2) and no file changed", out(r))

    def test_y04_rerun_mixed_call_with_a_nonzero_summary_fails(self):
        e = Env(self.addCleanup)
        e.make_run()
        self.transcript(e, self.mixed(e, [self.RERUN_OUT, self.RERUN_OUT.replace("created=0", "created=1")]))
        self.hashes(e, [])
        r = e.smv("init-check", "rerun", "--before", "before", "--after", "after")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn("FAIL  an apply run", out(r))

    def test_y04_rerun_mixed_call_with_a_changed_file_fails(self):
        e = Env(self.addCleanup)
        e.make_run()
        self.transcript(e, self.mixed(e, [self.RERUN_OUT, self.RERUN_OUT]))
        (e.proj / "AGENTS.md").write_text("before\n")
        self.assertEqual(e.smv("hash", "--label", "before").returncode, 0)
        (e.proj / "AGENTS.md").write_text("after\n")
        self.assertEqual(e.smv("hash", "--label", "after").returncode, 0)
        r = e.smv("init-check", "rerun", "--before", "before", "--after", "after")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn("FAIL  an apply run", out(r), "a mixed call never counts when a file changed")

    def test_y04_fresh_mixed_call_is_never_proof(self):
        e = Env(self.addCleanup)
        e.make_run()
        self.transcript(e, self.mixed(e, [self.scaffold_out(True), self.scaffold_out(False)]))
        self.hashes(e, ["AGENTS.md", "docs/x.md"])
        r = e.smv("init-check", "fresh", "--before", "before", "--after", "after")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn("FAIL  an apply run", out(r))

    def test_y04_apply_result_correlated_by_id_passes(self):
        e = Env(self.addCleanup)
        e.make_run()
        script = f'python3 "{e.lead}/skills/init/scaffold.py" --target . --stack none'
        self.transcript(e, [self.call(1, script + " --dry-run"), self.result(2, 1, self.scaffold_out(True)),
                            self.call(3, script), self.result(4, 3, self.scaffold_out(False)),
                            self.echo(5, "# summary: created=2 merged=0 skipped=0")])
        self.hashes(e, ["AGENTS.md", "docs/x.md"])
        r = e.smv("init-check", "fresh", "--before", "before", "--after", "after")
        self.assertEqual(r.returncode, 0, out(r))


class Isolation(unittest.TestCase):
    TREE = "print(json.dumps(m.lead_tree(Path(sys.argv[1]))))"

    def test_y07_ignored_env_change_is_seen(self):
        e = Env(self.addCleanup)
        (e.lead / ".env").write_text("A=1\n")
        before = e.py(self.TREE, str(e.lead)).stdout
        (e.lead / ".env").write_text("A=2\n")
        after = e.py(self.TREE, str(e.lead)).stdout
        self.assertTrue(before.strip())
        self.assertNotEqual(before, after, "a gitignored file's content change must show in the lead-tree snapshot")

    def test_y13_slack_state_content_change_is_seen(self):
        e = Env(self.addCleanup)
        e.make_run()
        state = e.home / ".local/state/supervisor-mesh-slack"
        state.mkdir(parents=True)
        code = ("run = m.Run(Path(sys.argv[1]))\nprint(json.dumps(m.user_state(run)['slack_default_state']))")
        (state / "state.json").write_text('{"ts": "1"}')
        before = e.py(code, str(e.run_root)).stdout
        (state / "state.json").write_text('{"ts": "2"}')
        after = e.py(code, str(e.run_root)).stdout
        self.assertTrue(before.strip() and before.strip() != "null")
        self.assertNotEqual(before, after)

    def test_y16_two_concurrent_writers_lose_nothing(self):
        e = Env(self.addCleanup)
        e.make_run()
        code = "run = m.Run(Path(sys.argv[1]))\nfor _ in range(150):\n    run.next_id('x')\n"
        procs = [subprocess.Popen(e.py_argv(code, str(e.run_root)), env=e.env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL) for _ in range(2)]
        for p in procs:
            p.wait(120)
        self.assertEqual(e.run_json()["step"], 300, "every step id of both writers is counted")


class Commands(unittest.TestCase):
    def test_y08_shell_reading_user_config_is_refused(self):
        e = Env(self.addCleanup)
        e.make_run()
        r = e.smv("cmd", "--label", "sh", "--", "sh", "-c", 'cat "$HOME/.claude/settings.json"')
        self.assertNotEqual(r.returncode, 0, out(r))
        self.assertNotIn(e.marker["CONFIG"], e.evidence_text())

    def test_y08_relative_path_into_user_config_is_refused(self):
        e = Env(self.addCleanup)
        e.make_run()
        rel = os.path.relpath(e.cfg / "settings.json", e.proj)
        r = e.smv("cmd", "--label", "cat", "--", "cat", rel)
        self.assertNotEqual(r.returncode, 0, out(r))
        self.assertNotIn(e.marker["CONFIG"], e.evidence_text())

    def test_y08_step_output_is_redacted(self):
        e = Env(self.addCleanup)
        e.make_run()
        token = "xox" + "b-" + "1234567890abcdef"
        (e.proj / "notes.txt").write_text(f"mail {e.email}\nkey {token}\nplain line\n")
        self.assertEqual(e.smv("cmd", "--label", "cat", "--", "cat", "notes.txt").returncode, 0)
        r = e.smv("cmd", "--label", "token-scan", "--", "grep", "-rEn", "(xox[abeprs][.-]|xapp-)[A-Za-z0-9-]{8,}", ".")
        self.assertEqual(r.returncode, 0, out(r))
        text = e.evidence_text()
        self.assertNotIn(e.email, text)
        self.assertNotIn(token, text)
        self.assertIn("plain line", text)

    def test_y09_wrapped_address_in_a_kept_line_is_dropped(self):
        e = Env(self.addCleanup)
        local = f"smv.wrap.{e.rand}"
        screen = "\n".join(["reply", RULE, f"❯ {{box}} mail {local}@exa", "  mple.com today", RULE, "  footer"])
        e.make_run(screen=screen)
        self.assertEqual(e.smv("capture", "--label", "wrapped").returncode, 0)
        self.assertNotIn(f"{local}@exa", e.evidence_text())

    def test_y10_tool_output_preview_never_reaches_a_capture(self):
        e = Env(self.addCleanup)
        e.make_run(screen=ready_screen("⏺ Bash(cat registry.json)", f'  ⎿  {{"x": "{e.marker["PREVIEW"]}"}}'))
        self.assertEqual(e.smv("capture", "--label", "preview").returncode, 0)
        self.assertNotIn(e.marker["PREVIEW"], e.evidence_text())
        cap = sorted((e.run_root / "evidence/panes").glob("*.txt"))[-1].read_text()
        self.assertTrue(cap.startswith("# screen sha256 "), cap[:80])

    def test_y11_prefix_cannot_leave_the_scratch_root(self):
        e = Env(self.addCleanup)
        for prefix in ("../escape", str(e.base / "abs-escape")):
            r = e.smv("launch", "--scratch-root", str(e.scratch), "--prefix", prefix, "--timeout", "30")
            self.assertEqual(r.returncode, 2, out(r))
        self.assertEqual(sorted(p.name for p in e.base.iterdir() if "escape" in p.name), [])

    def test_y12_mkt_project_retry_keeps_ownership(self):
        e = Env(self.addCleanup)
        e.make_run()
        for _ in range(2):
            r = e.smv("mkt-project")
            self.assertEqual(r.returncode, 0, out(r))
        self.assertIs(e.run_json()["marketplace"]["registry_preexisting"], False)

    def test_exit_21_on_another_marketplace(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.state["marketplaces"] = [{"name": "supervisor-mesh", "source": "directory", "path": str(e.base / "other")}]
        e.save()
        self.assertEqual(e.smv("mkt-project").returncode, 21)

    def test_y20_hash_diff_normalises_allowed_paths(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.smv("hash", "--label", "a")
        (e.proj / "docs").mkdir()
        (e.proj / "docs/x.md").write_text("x\n")
        e.smv("hash", "--label", "b")
        r = e.smv("hash-diff", "a", "b", "--allow", "ADDED ./docs/x.md")
        self.assertEqual(r.returncode, 0, out(r))

    def test_allow_guard_refusals(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.smv("hash", "--label", "a")
        for spec in (f"CHANGED {e.cfg / 'settings.json'}", "CHANGED ~/.claude/settings.json", "ADDED ../x",
                     f"ADDED {e.lead / 'AGENTS.md'}", "ADDED .claude/plugins/installed_plugins.json"):
            r = e.smv("hash-diff", "a", "a", "--allow", spec)
            self.assertEqual(r.returncode, 2, spec + out(r))
        self.assertEqual(len(e.run_json()["allowances"]), 5)

    def test_y14_seed_writes_several_lines_into_a_new_file_only(self):
        e = Env(self.addCleanup)
        e.make_run()
        r = e.smv("seed", "--file", "docs/tracker.md", "--line", "# Tracker", "--line", "| WO-SMV-1 | qa | open |")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertEqual((e.proj / "docs/tracker.md").read_text(), "# Tracker\n| WO-SMV-1 | qa | open |\n")
        self.assertNotEqual(e.smv("seed", "--file", "docs/tracker.md", "--line", "again").returncode, 0)

    def test_y20_resume_commands_are_pasteable(self):
        e = Env(self.addCleanup)
        r = e.py("print(json.dumps(m.RESUME))")
        resume = json.loads(r.stdout)
        self.assertTrue(all(v.startswith('"$SMV" ') for v in resume.values()), resume)
        skill = (SKILL / "SKILL.md").read_text()
        self.assertEqual([v for v in resume.values() if f"`{v}`" not in skill], [], "SKILL.md quotes every one")


if __name__ == "__main__":
    unittest.main()
