"""The evidence redaction self-test (`smv selftest-redaction`, and tests/test_redaction.py).

It plants unique markers where user data lives: tool_result bodies (SMV-MARKER-TOOLRESULT), fixture user config
files and a thinking block (SMV-MARKER-CONFIG), a CLAUDE.md system reminder and injected skill text
(SMV-MARKER-CLAUDEMD), and a tool-output preview on the pane screen (SMV-MARKER-PREVIEW), plus an e-mail address and
a /home/<user>/.claude path (each also wrapped across two screen lines). Then it runs the whole evidence path
(tgrep in every mode, init-check, spawn-check, msg-status, capture, cleanup) against the stub tools and greps the
evidence dir. It passes only when no planted string is there and every check still passes on the proof lines.
"""
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess

from harness import RULE, Env, ready_screen


def transcripts(e: Env, mark: dict, cfg_path: str, t0: dt.datetime) -> tuple[list, list]:
    """A PM and a role transcript shaped like live run #4, with correlated tool_use ids."""
    def ts(i):
        return (t0 + dt.timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def user(i, content, **kw):
        return {"type": "user", "timestamp": ts(i), "message": {"role": "user", "content": content}, **kw}

    def reply(i, *blocks):
        return {"type": "assistant", "timestamp": ts(i), "message": {"role": "assistant", "content": list(blocks)}}

    def call(i, name, **inp):
        return {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": inp}

    def result(i, call_i, body):
        return user(i, [{"type": "tool_result", "tool_use_id": f"toolu_{call_i}", "content": body}])

    def text(t):
        return {"type": "text", "text": t}

    cfg = e.cfg
    claude_md = (cfg / "CLAUDE.md").read_text()
    reminder = (f"<system-reminder>\nContents of {cfg}/CLAUDE.md (user's private global instructions):\n{claude_md}"
                f"<command-args>{mark['CLAUDEMD']}</command-args>\nThe user's email address is {e.email}.\n"
                "</system-reminder>\n")
    scaffold = (f"# supervisor-mesh init → {e.proj}  (stack: none{{}})\nCREATE  AGENTS.md\nCREATE  docs/tracker.md\n"
                f"# summary: created=2 merged=0 skipped=0\n# next: fill docs/harness/part-b-checklist.md "
                f"{mark['TOOLRESULT']}\n")
    run_scaffold = f'python3 "{e.lead}/skills/init/scaffold.py" --target "{e.proj}" --stack none'
    registry = (cfg / "plugins/installed_plugins.json").read_text()
    pm = [user(1, reminder + "<command-message>supervisor-mesh:init</command-message>\n"
                  "<command-name>/supervisor-mesh:init</command-name>\n<command-args>--stack none</command-args>"),
          user(2, [text(f"Base directory for this skill: {cfg}/plugins/cache/x\n{mark['CLAUDEMD']}")], isMeta=True),
          reply(3, {"type": "thinking", "thinking": f"the registry says {mark['CONFIG']}"},
                call(3, "Read", file_path=str(cfg / "plugins/installed_plugins.json"))),
          result(4, 3, [text(registry + f"\nCREATE  {mark['TOOLRESULT']}\n# summary: created=9 merged=0 skipped=0")]),
          reply(5, call(5, "Bash", command=run_scaffold + " --dry-run")),
          result(6, 5, scaffold.format(", dry-run")),
          reply(7, call(7, "Bash", command=run_scaffold)),
          result(8, 7, scaffold.format("")),
          reply(9, call(9, "Bash", command=f"cat {cfg}/settings.json")),
          result(10, 9, (cfg / "settings.json").read_text() + f"\n{mark['TOOLRESULT']}"),
          reply(11, call(11, "Bash", command="projmux agent message send role-qa WO-1")),
          result(12, 11, f"message-{os.urandom(12).hex()} delivered\n{mark['TOOLRESULT']} {e.email}"),
          reply(13, text("Init applied.\n# summary: created=2 merged=0 skipped=0 (rows read from "
                         "~/.claude/plugins/installed_plugins.json)")),
          reply(14, text(f"Checked {cfg_path} first\nDISPATCH-RECEIVED SMV-ORDER-1\nreply to {e.email} when done"))]
    role = [user(1, reminder + "[supervisor-mesh work order] WO-1"),
            reply(2, text("ROLE LOADED role-lead-qa\nready for work orders")),
            reply(3, call(3, "Read", file_path=str(cfg / "CLAUDE.md"))),
            result(4, 3, claude_md + mark["TOOLRESULT"])]
    return pm, role


def run(keep: bool = False, verbose: bool = False) -> int:
    e = Env()
    mark = e.marker
    cfg_path = "/home/" + "smvselftest/.claude/settings.json"
    wrapped_local = f"smv.wrap.{e.rand}"
    planted = {**{f"marker {k}": v for k, v in mark.items()}, "e-mail address": e.email,
               "user config path": cfg_path, "wrapped e-mail (local part)": wrapped_local + "@exa",
               "wrapped config path": "/home/" + "smvwrapped/.cla"}
    screen = ready_screen("> /supervisor-mesh:init --stack none", f"⏺ Read({e.cfg}/plugins/installed_plugins.json)",
                          "  ⎿  Read 9 lines", "⏺ Bash(cat registry.json)",
                          f'  ⎿  {{"plugins": {{"{mark["PREVIEW"]}": []}}}}', f"  contact {e.email}")
    box = (f"{RULE}\n❯ {{box}} mail {wrapped_local}@exa\n  mple.com from " + "/home/"
           + f"smvwrapped/.cla\n  ude/settings.json\n{RULE}")  # an address and a config path the box wrapped
    screen = screen.replace(f"{RULE}\n❯ {{box}}\n{RULE}", box)
    pm_t = e.cfg / "projects/-selftest/pm.jsonl"
    qa_t = e.cfg / "projects/-selftest/qa.jsonl"
    pm = e.make_run(screen=screen, pm_transcript=pm_t)
    qa = e.agent("role-qa", {"role": "qa", "mesh": "supervisor-mesh"}, pm["metadata"]["uid"], qa_t)
    e.state["agents"].append(qa)
    e.add_pane(qa, "%2", ready_screen("  ready"))
    e.save()
    t0 = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    pm_recs, qa_recs = transcripts(e, mark, cfg_path, t0)
    pm_t.parent.mkdir(parents=True)
    for path, recs in ((pm_t, pm_recs), (qa_t, qa_recs)):
        path.write_text("".join(json.dumps(r) + "\n" for r in recs))
    data = e.run_json()
    data["last_send"] = t0.isoformat()
    (e.run_root / "evidence/run.json").write_text(json.dumps(data, indent=1))
    checks: list[tuple[str, bool, str]] = []

    def step(*args: str) -> str:
        r = e.smv(*args)
        checks.append((f"smv {' '.join(args)}", r.returncode == 0, f"exit {r.returncode}"))
        if r.returncode != 0 or verbose:
            print(r.stdout + r.stderr, end="")
        return r.stdout

    step("hash", "--label", "before")
    (e.proj / "docs").mkdir()
    for rel in ("AGENTS.md", "docs/tracker.md"):
        (e.proj / rel).write_text("scaffold output\n")
    step("hash", "--label", "after")
    step("tgrep", "--since-last-send", "--assistant-only", "--pattern", "DISPATCH-RECEIVED")
    step("tgrep", "--tool-inputs", "--pattern", r"skills/init/scaffold\.py")
    step("tgrep", "--since-last-send", "--pattern", r"# summary: created=[0-9]+")
    step("tgrep", "--absent", "--pattern", "SMV-NEVER-SAID")
    init = step("init-check", "fresh", "--before", "before", "--after", "after")
    step("spawn-check", "--role", "qa", "--wait", "0")
    step("msg-status")
    step("capture", "--label", "screen")
    step("cleanup")
    ev = e.run_root / "evidence"
    sources = [pm_t, qa_t, *(p for p in e.cfg.rglob("*") if p.is_file() and "projects" not in p.parts)]
    for name, needle in planted.items():
        count = sum(p.read_text().count(needle) for p in sources) + screen.count(needle)
        g = subprocess.run(["grep", "-rFc", "--", needle, str(ev)], capture_output=True, text=True, timeout=60)
        hits = sum(int(x.rsplit(":", 1)[1]) for x in g.stdout.splitlines())
        scan = sum(p.read_bytes().count(needle.encode()) for p in ev.rglob("*") if p.is_file())
        checks.append((f"{name}: 0 hits in the evidence dir", count > 0 and scan == 0 and hits == 0,
                       f"planted {count}, evidence {scan} (grep -rF exit {g.returncode})"))
    pm_uid, qa_uid = pm["metadata"]["uid"], qa["metadata"]["uid"]
    pm_ext, qa_ext = (ev / "transcripts" / f"{u}.extract.txt" for u in (pm_uid, qa_uid))
    pm_text, qa_text = (p.read_text() if p.is_file() else "" for p in (pm_ext, qa_ext))
    want = ["command-name: /supervisor-mesh:init", "command-args: --stack none", "tool_use: Read",
            "tool_use: Bash [scaffold.py apply, target scratch project, id toolu_7]",
            "scaffold result: # summary: created=2 merged=0 skipped=0 [result of toolu_7]",
            "scaffold result: CREATE  AGENTS.md [result of toolu_7]", "match: DISPATCH-RECEIVED SMV-ORDER-1",
            "receipt: message-"]
    checks.append(("PM extract keeps the proof lines", all(w in pm_text for w in want),
                   f"missing {[w for w in want if w not in pm_text]}"))
    proof = pm_text.split("## check matches")[0]
    checks.append(("a summary inside a non-scaffold tool result is never a proof line", "created=9" not in proof,
                   "the Read result of installed_plugins.json"))
    checks.append(("role extract keeps ROLE LOADED with its context", "match: ROLE LOADED role-lead-qa" in qa_text
                   and "after: ready for work orders" in qa_text, qa_ext.name))
    checks.append(("init-check proved the apply run by its tool_use_id", "toolu_7: created=2 merged=0 skipped=0"
                   in init and "init-check fresh: PASS" in init, "see the init-check step"))
    meta = json.loads((ev / "transcripts" / f"{pm_uid}.meta.json").read_text()) if pm_ext.is_file() else {}
    raw = pm_t.read_bytes()
    reads = meta.get("reads") or []
    checks.append(("meta keeps path, sha256 and size of every read", bool(reads) and all(
        r.get("sha256") == hashlib.sha256(raw).hexdigest() and r.get("size") == len(raw) for r in reads)
                   and meta.get("path") == "~/.claude/projects/-selftest/pm.jsonl",
                   f"{len(reads)} read(s), path {meta.get('path')}"))
    dropped = [sum((meta.get(k) or {}).values()) for k in ("dropped_proof_lines", "dropped_match_lines")]
    checks.append(("redaction dropped and counted the e-mail / config-path lines", dropped[0] >= 1 and dropped[1] >= 2,
                   f"proof lines {meta.get('dropped_proof_lines')}, match lines {meta.get('dropped_match_lines')}"))
    copies = sorted(p.name for p in (ev / "transcripts").iterdir()
                    if not p.name.endswith((".meta.json", ".extract.txt")))
    checks.append(("no transcript copy in evidence (only .meta.json and .extract.txt)", not copies, str(copies)))
    panes = sorted((ev / "panes").glob("*.txt"))
    heads = all(p.read_text().startswith("# screen sha256 ") for p in panes)
    wrapped = sum(p.read_text().count("(wrapped)]") for p in panes)
    checks.append(("captures keep only the needed lines, with the full-screen sha256", bool(panes) and heads,
                   f"{len(panes)} capture(s)"))
    checks.append(("wrapped e-mail and config path dropped as wrapped", wrapped >= 2, f"{wrapped} placeholder(s)"))
    bad = sum(1 for _, good, _ in checks if not good)
    for name, good, detail in checks:
        print(f"{'PASS' if good else 'FAIL'}  {name}: {detail}")
    verdict = "PASS" if not bad else f"FAIL ({bad})"
    print(f"selftest-redaction ({len(planted)} planted strings, evidence {ev}): {verdict}")
    if keep:
        print(f"kept: {e.base}")
    else:
        shutil.rmtree(e.base)
        print(f"removed {e.base}")
    return 0 if not bad else 1
