"""The scratch-named marketplace (WO-0024 design): the user-level 'supervisor-mesh' entry (GitHub source) must end
every run exactly as it started, because a project-scope `marketplace add` of the same name rewrites the shared
registry entry by name (the stub claude models that). So a run exports the commit under test, renames only the
manifest's `name`, declares that export at project scope, installs `supervisor-mesh@<scratch>`, and cleanup removes
the scratch name only. Run against an older smv.py (SMV_UNDER_TEST) to see the red result."""
import json
import shutil
import unittest

from harness import GUARD, GUARD_ROW, Env


def out(r) -> str:
    return r.stdout + r.stderr


def guarded_env(t: unittest.TestCase) -> Env:
    e = Env(t.addCleanup)
    e.state["marketplaces"] = [dict(GUARD_ROW)]
    e.save()
    e.make_run()
    return e


def install_from_export(e: Env) -> None:
    """What /plugin install + scope-pick leave behind: a project-scope row whose installPath is a copy of the
    export, and the scratch settings enabling the plugin."""
    cache = e.base / "cache"
    shutil.copytree(e.run_root / "mkt-src", cache)
    e.load()
    e.state["plugin_rows"] = [{"id": e.plugin_id, "scope": "project", "projectPath": str(e.proj), "version":
                               json.loads((cache / ".claude-plugin/plugin.json").read_text())["version"],
                               "enabled": True, "installPath": str(cache)}]
    e.save()
    settings = e.proj / ".claude/settings.json"
    data = json.loads(settings.read_text())
    data["enabledPlugins"] = {e.plugin_id: True}
    settings.write_text(json.dumps(data))


class ScratchMarketplace(unittest.TestCase):
    def test_guard_entry_is_never_added_or_rewritten(self):
        e = guarded_env(self)
        r = e.smv("mkt-project")
        self.assertEqual(r.returncode, 0, out(r))
        adds = e.calls("claude", "plugin", "marketplace", "add")
        self.assertEqual([a[3] for a in adds], [str(e.run_root / "mkt-src")], "the export, never the checkout")
        guard = [m for m in e.load()["marketplaces"] if m["name"] == GUARD]
        self.assertEqual(guard, [GUARD_ROW], "the user's GitHub entry is byte-for-byte what it was")
        declared = json.loads((e.proj / ".claude/settings.json").read_text())["extraKnownMarketplaces"]
        self.assertEqual(sorted(declared), [e.mkt_name])
        rec = e.run_json()["marketplace"]
        self.assertEqual((rec["name"], rec["plugin_id"], rec["guard"]["type"]), (e.mkt_name, e.plugin_id, "github"))
        self.assertIn(f"PLUGIN_ID={e.plugin_id}", out(r))
        self.assertIn(f"PASS  user marketplace '{GUARD}' source unchanged (before vs after the add)", out(r))

    def test_export_is_the_commit_with_only_the_manifest_name_changed(self):
        e = guarded_env(self)
        (e.lead / "skills/init/scaffold.py").write_text("# an uncommitted working-tree edit\n")
        r = e.smv("mkt-project")
        self.assertEqual(r.returncode, 0, out(r))
        src = e.run_root / "mkt-src"
        self.assertEqual((src / "skills/init/scaffold.py").read_text(), "# test placeholder\n", "commit, not tree")
        self.assertEqual(json.loads((src / ".claude-plugin/marketplace.json").read_text())["name"], e.mkt_name)
        ev = e.run_root / "evidence/export"
        self.assertEqual((ev / "manifest-keypaths.txt").read_text().splitlines()[1:], ["name"])
        tree = (ev / "tree.txt").read_text()
        self.assertNotIn("DIFFERS", tree)
        self.assertNotIn("missing", tree)
        self.assertEqual(sorted(e.git_lead("ls-tree", "-r", "--name-only", "HEAD").split()),
                         sorted(p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file()))
        self.assertFalse((e.run_root / "mkt-src.tar").exists(), "the archive file is removed after extraction")

    def test_export_checks_catch_a_changed_extra_or_renamed_file(self):
        e = guarded_env(self)
        self.assertEqual(e.smv("mkt-project").returncode, 0)
        head = e.git_lead("rev-parse", "HEAD").strip()
        code = ("run = m.Run.load(None)\n"
                "src = run.mkt_src\n"
                "print(json.dumps([c[1] for c in m.export_checks(run, sys.argv[1], src, run.mkt_name)]))\n"
                "(src / 'agents').mkdir(exist_ok=True)\n"
                "(src / 'agents/extra.md').write_text('x')\n"
                "(src / 'skills/init/scaffold.py').write_text('changed')\n"
                "mf = src / '.claude-plugin/marketplace.json'\n"
                "d = json.loads(mf.read_text()); d['owner']['name'] = 'other'; mf.write_text(json.dumps(d))\n"
                "print(json.dumps([c[1] for c in m.export_checks(run, sys.argv[1], src, run.mkt_name)]))\n")
        r = e.py(code, head)
        self.assertEqual(r.returncode, 0, out(r))
        clean, tampered = (json.loads(x) for x in r.stdout.splitlines()[-2:])
        self.assertTrue(all(clean), clean)
        self.assertEqual(tampered[:3], [False, False, False], "extra file, changed blob, second manifest key path")

    def test_commit_parameter(self):
        e = guarded_env(self)
        for bad in ("--upload-pack=x", "no-such-ref"):
            r = e.smv("mkt-project", f"--commit={bad}")
            self.assertEqual(r.returncode, 2, bad + out(r))
        self.assertEqual(e.calls("claude", "plugin", "marketplace", "add"), [])
        first = e.git_lead("rev-parse", "HEAD").strip()
        self.assertEqual(e.smv("mkt-project", "--commit", first).returncode, 0)
        self.assertEqual(e.run_json()["marketplace"]["commit"], first)
        (e.lead / "README.md").write_text("second\n")
        e.git_lead("add", "README.md")
        e.git_lead("commit", "-q", "-m", "second")
        r = e.smv("mkt-project", "--commit", "HEAD")
        self.assertEqual(r.returncode, 2, "one commit per run: " + out(r))
        self.assertEqual(e.smv("mkt-project", "--commit", first).returncode, 0, "a retry of the same commit is fine")

    def test_exit_21_when_the_scratch_name_is_taken(self):
        e = Env(self.addCleanup)
        e.state["marketplaces"] = [{"name": e.mkt_name, "source": "directory", "path": str(e.base / "other")}]
        e.save()
        e.make_run()
        self.assertEqual(e.smv("mkt-project").returncode, 21)
        self.assertEqual(e.calls("claude", "plugin", "marketplace", "add"), [])

    def test_plugin_check_compares_the_install_with_the_export(self):
        e = guarded_env(self)
        self.assertEqual(e.smv("mkt-project").returncode, 0)
        install_from_export(e)
        r = e.smv("plugin-check")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertIn("PASS  installed copy == commit export", out(r))
        (e.base / "cache/.mcp.json").write_text("{}")
        r = e.smv("plugin-check")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn("FAIL  installed copy == commit export (shipped dirs): 1 differing: ['.mcp.json']", out(r))

    def test_cleanup_removes_only_the_scratch_name_at_project_scope(self):
        e = guarded_env(self)
        self.assertEqual(e.smv("mkt-project").returncode, 0)
        install_from_export(e)
        r = e.smv("cleanup")
        self.assertEqual(r.returncode, 0, out(r))
        removes = e.calls("claude", "plugin", "marketplace", "remove")
        self.assertEqual(removes, [["plugin", "marketplace", "remove", e.mkt_name, "--scope", "project", "--json"]])
        self.assertEqual(e.calls("claude", "plugin", "uninstall")[0][2], e.plugin_id)
        self.assertEqual(e.load()["marketplaces"], [GUARD_ROW])
        self.assertIn(f"PASS  scratch marketplace '{e.mkt_name}' gone from the registry", out(r))
        self.assertIn(f"PASS  user marketplace '{GUARD}' source unchanged (cleanup vs baseline)", out(r))
        self.assertFalse((e.run_root / "mkt-src").exists(), "the export goes with the scratch project")

    def test_cleanup_fails_while_the_scratch_entry_is_still_registered(self):
        e = guarded_env(self)
        self.assertEqual(e.smv("mkt-project").returncode, 0)
        e.load()
        e.state["remove_keeps_registry"] = True
        e.save()
        r = e.smv("cleanup")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn(f"LEFT scratch marketplace registry entry '{e.mkt_name}'", out(r))
        self.assertTrue((e.run_root / "mkt-src").is_dir(), "a failed cleanup keeps the export")

    def test_a_rewritten_guard_entry_fails_doctor_and_cleanup(self):
        e = guarded_env(self)
        self.assertEqual(e.smv("mkt-project").returncode, 0)
        e.load()
        e.state["marketplaces"] = [m if m["name"] != GUARD else {"name": GUARD, "source": "directory",
                                                                 "path": str(e.lead)} for m in e.state["marketplaces"]]
        e.save()
        r = e.smv("doctor")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn(f"FAIL  user marketplace '{GUARD}' source unchanged (doctor vs baseline): github", out(r))
        self.assertIn(f"FAIL  user marketplace '{GUARD}' source unchanged (doctor vs mkt-project)", out(r))
        r = e.smv("cleanup")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn(f"user marketplace '{GUARD}' source changed", out(r))
        self.assertEqual([a for a in e.calls("claude", "plugin", "marketplace", "remove") if GUARD in a[3:4]], [])

    def registry_file(self, e: Env):
        return e.cfg / "plugins/known_marketplaces.json"

    def test_a_refresh_timestamp_is_no_fail_but_a_new_name_is(self):
        e = guarded_env(self)
        self.assertEqual(e.smv("mkt-project").returncode, 0)
        reg = json.loads(self.registry_file(e).read_text())
        self.assertEqual(sorted(reg), ["other", e.mkt_name], "baseline names plus exactly the scratch name")
        reg["other"]["lastUpdated"] = "refreshed"
        self.registry_file(e).write_text(json.dumps(reg))
        r = e.smv("doctor")
        self.assertNotIn("known_marketplaces.json marketplace names", out(r))
        self.assertNotIn("FAIL  isolation", out(r))
        reg["stray"] = {"source": {"source": "github"}}
        self.registry_file(e).write_text(json.dumps(reg))
        r = e.smv("doctor")
        self.assertIn("known_marketplaces.json marketplace names: +['stray']", out(r))

    def test_installed_plugins_may_keep_scratch_bookkeeping_only(self):
        e = guarded_env(self)
        self.assertEqual(e.smv("mkt-project").returncode, 0)
        install_from_export(e)
        ip = e.cfg / "plugins/installed_plugins.json"
        data = json.loads(ip.read_text())
        data["plugins"][e.plugin_id] = [{"scope": "project", "left": "by uninstall, by design"}]
        ip.write_text(json.dumps(data))
        r = e.smv("cleanup", "--keep-proj")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertIn(f"run-owned key paths ['ADDED plugins.{e.plugin_id}']", out(r))
        data["plugins"]["other@other"] = [{"scope": "user", "changed": True}]
        ip.write_text(json.dumps(data))
        r = e.py("run = m.Run.load(None)\nprint(m.isolation_checks(run, 'again', 'cleanup'))")
        self.assertIn(f"changed key paths that do not name '@{e.mkt_name}': ['CHANGED plugins.other@other']",
                      out(r))

    def test_installed_plugins_oracle(self):
        """Only @<scratch> keys may change (sha256 is info); a foreign key, or a user-scope entry appearing or
        changing (also under the scratch id), is a FAIL."""
        e = guarded_env(self)
        ip = e.cfg / "plugins/installed_plugins.json"
        base = json.loads(ip.read_text())
        check = "run = m.Run.load(None)\nprint(json.dumps(m.isolation_checks(run, sys.argv[1], 'doctor')))"
        cases = {
            "scratch-only": ({e.plugin_id: [{"scope": "project", "installPath": "x"}]}, True),
            "foreign-key": ({"other@other": [{"scope": "user", "installPath": "changed"}]}, False),
            "user-row-added": ({e.plugin_id: [{"scope": "user", "installPath": "x"}]}, False),
        }
        for label, (plugins, good) in cases.items():
            ip.write_text(json.dumps({**base, "plugins": {**base["plugins"], **plugins}}))
            r = e.py(check, label)
            self.assertEqual(r.returncode, 0, out(r))
            fails = [c for c in json.loads(r.stdout) if not c[1]]
            self.assertEqual(not fails, good, f"{label}: {fails}")
            if label == "foreign-key":
                self.assertIn("CHANGED plugins.other@other", str(fails))
            if label == "user-row-added":
                self.assertIn(f"user-scope entries changed for ['{e.plugin_id}']", str(fails))
        ip.write_text(json.dumps(base))

    def test_scratch_name_rule(self):
        e = Env(self.addCleanup)
        r = e.py("print(json.dumps([m.scratch_name(x) for x in ('smv-20261118-42', 'Run_A', 'run-ab')]))")
        self.assertEqual(json.loads(r.stdout), ["smv-20261118-42", "smv-run-a", "smv-run-ab"])
        r = e.py("m.scratch_name('supervisor-mesh'[:0] + '---')")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
