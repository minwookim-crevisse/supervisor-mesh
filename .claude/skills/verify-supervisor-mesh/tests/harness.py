"""Offline harness for the smv tests: a fake HOME and Claude config dir, a fake lead repo, stub projmux / tmux /
claude on PATH (stub_tools.py), a run directory, and helpers to call smv. No real projmux, tmux or claude is ever
called, and nothing outside the test's temp directory ($TMPDIR) is written.

The module under test is $SMV_UNDER_TEST (default: ../bin/smv.py), so the same tests can run against an older
copy to show a red result before a fix.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SKILL = TESTS.parent
CHECKOUT = SKILL.parents[2]
SMV = Path(os.environ.get("SMV_UNDER_TEST") or SKILL / "bin/smv.py").resolve()
RULE = "─" * 60
PLUGIN_ID = "supervisor-mesh@supervisor-mesh"


def load_smv():
    """The smv module under test, imported by path (no bytecode is written)."""
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SMV.parent))
    spec = importlib.util.spec_from_file_location("smv_under_test", SMV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def uid(kind: str) -> str:
    return f"{kind}-{os.urandom(12).hex()}"


def ready_screen(*above: str, footer: str = "  ⏵⏵ auto mode on (shift+tab to cycle)") -> str:
    """A ready Claude Code screen: transcript lines, then the input box with a {box} slot, then the footer."""
    return "\n".join([" ▐▛███▜▌   Claude Code v2.1.307", "", *above, "", RULE, "❯ {box}", RULE, footer])


RUN_SNIPPET = r"""
import importlib.util, json, sys
from pathlib import Path
smv_path, root, lead, data, baseline = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), json.loads(sys.argv[4]), \
    sys.argv[5] == "1"
sys.path.insert(0, str(Path(smv_path).parent))
spec = importlib.util.spec_from_file_location("smv_under_test", smv_path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
run = m.Run.create(root, lead, root.name)
run.proj.mkdir()
m.sh(["git", "init", "-q", str(run.proj)])
run.file.write_text(json.dumps({**json.loads(run.file.read_text()), **data}, indent=1))
run = m.Run(root)
m.digest_key(run, create=True)
if baseline:
    m.write_snapshot(run, "baseline")
"""


class Env:
    """One isolated world per test. `cleanup` is called with the remover (unittest's addCleanup)."""

    def __init__(self, cleanup=None):
        self.base = Path(tempfile.mkdtemp(prefix="smv-test-"))
        if cleanup:
            cleanup(shutil.rmtree, self.base, True)
        self.rand = os.urandom(5).hex()
        self.home, self.lead, self.scratch, self.bin = (self.base / x for x in ("home", "lead", "scratch", "bin"))
        self.cfg = self.home / ".claude"
        self.state_p, self.log_p = self.base / "stub-state.json", self.base / "stub-calls.jsonl"
        self.marker = {k: f"SMV-MARKER-{k}-{self.rand}" for k in ("CONFIG", "CLAUDEMD", "TOOLRESULT", "PREVIEW")}
        self.email = f"smv.test.{self.rand}" + "@" + "example.com"
        version = json.loads((CHECKOUT / ".claude-plugin/plugin.json").read_text())["version"]
        files = {
            self.cfg / "settings.json": json.dumps({"permissions": {"allow": [f"Bash({self.marker['CONFIG']})"]},
                                                   "env": {"SMV_NOTE": self.marker["CONFIG"]}}, indent=1),
            self.cfg / "plugins/installed_plugins.json": json.dumps(
                {"version": 2, "plugins": {"other@other": [{"scope": "user", "installPath": self.marker["CONFIG"]}]}},
                indent=1),
            self.cfg / "plugins/known_marketplaces.json": json.dumps({"other": {"source": {
                "path": self.marker["CONFIG"]}}}),
            self.cfg / "CLAUDE.md": f"# global rules\n- {self.marker['CLAUDEMD']}\n- contact {self.email}\n",
            self.lead / "scripts/scrub.sh": (CHECKOUT / "scripts/scrub.sh").read_text(),
            self.lead / ".claude-plugin/plugin.json": json.dumps({"name": "supervisor-mesh", "version": version}),
            self.lead / "skills/init/scaffold.py": "# test placeholder\n",
            self.lead / ".gitignore": ".env\n__pycache__/\n",
        }
        for path, body in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        self.scratch.mkdir()
        self.bin.mkdir()
        for tool in ("projmux", "tmux", "claude"):
            (self.bin / tool).write_text(f"#!{sys.executable} -B\nimport runpy\n"
                                         f"runpy.run_path({str(TESTS / 'stub_tools.py')!r}, "
                                         f"init_globals={{'TOOL': {tool!r}}}, run_name='__main__')\n")
            (self.bin / tool).chmod(0o755)
        self.env = {**os.environ, "HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.cfg),
                    "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}", "SMV_STUB_STATE": str(self.state_p),
                    "SMV_STUB_LOG": str(self.log_p), "SMV_REPO": str(self.lead), "PYTHONDONTWRITEBYTECODE": "1",
                    "GIT_CONFIG_NOSYSTEM": "1"}
        self.env.pop("SMV_RUN", None)
        subprocess.run(["git", "init", "-q", str(self.lead)], env=self.env, check=True)
        self.state = {"project": None, "registered": False, "agents": [], "deleted": [], "status": {}, "panes": {},
                      "tmux": {}, "plugin_rows": [], "marketplaces": [], "fail": {}}
        self.save()
        self.run_root = None

    # -- stub state
    def save(self) -> None:
        self.state_p.write_text(json.dumps(self.state, indent=1))

    def load(self) -> dict:
        self.state = json.loads(self.state_p.read_text())
        return self.state

    def calls(self, tool: str, *prefix: str) -> list[list[str]]:
        rows = [json.loads(x) for x in self.log_p.read_text().splitlines()] if self.log_p.is_file() else []
        return [r["args"] for r in rows if r["tool"] == tool and r["args"][:len(prefix)] == list(prefix)]

    def keys_sent(self, pane: str = "%1") -> list[list[str]]:
        return [a[a.index(pane) + 1:] for a in self.calls("tmux") if "send-keys" in a]

    # -- the run
    def agent(self, name: str, labels: dict | None = None, creator: str | None = None, transcript: Path | None = None,
              phase: str = "Running", pane: str | None = None, cwd: Path | None = None) -> dict:
        a = uid("agent")
        pane_uid = pane or uid("pane")
        return {"metadata": {"uid": a, "name": name, "labels": labels or {}, "ownerRef": {"uid": "win-1"},
                             "annotations": {"projmux.io/creator-agent": creator} if creator else {}},
                "spec": {"workspace": {"cwd": str(cwd or self.proj)}},
                "status": {"phase": phase, "paneRef": pane_uid,
                           "sessionRef": {"claude": {"transcriptPath": str(transcript) if transcript else None}}}}

    @property
    def proj(self) -> Path:
        return self.run_root / "proj"

    def make_run(self, screen: str | None = None, baseline: bool = True, registered: bool = True,
                 pm_transcript: Path | None = None, lead: Path | None = None) -> dict:
        """A run with a registered project and a recorded PM agent on tmux pane %1 showing `screen`. `lead` is the
        lead tree the run records (default: the fake lead repo; read-only either way)."""
        self.run_root = self.scratch / f"run-{self.rand}"
        pm = self.agent("pm", transcript=pm_transcript)
        project = uid("proj")
        data = {"pm": pm["metadata"]["uid"], "created": {"project": project, "agents": [pm["metadata"]["uid"]]}}
        subprocess.run([sys.executable, "-B", "-c", RUN_SNIPPET, str(SMV), str(self.run_root), str(lead or self.lead),
                        json.dumps(data), "1" if baseline else "0"], env=self.env, check=True)
        self.state.update(project=project, registered=registered, agents=[pm], root=str(self.proj))
        self.add_pane(pm, "%1", screen or ready_screen())
        self.env["SMV_RUN"] = str(self.run_root)
        self.save()
        return pm

    def add_pane(self, agent: dict, tmux_id: str, screen: str, **extra) -> None:
        self.state["panes"][agent["status"]["paneRef"]] = tmux_id
        self.state["tmux"][tmux_id] = {"screens": {"main": screen}, "screen": "main", "box": "", "on_enter": [],
                                       **extra}

    def pane(self, tmux_id: str = "%1") -> dict:
        return self.state["tmux"][tmux_id]

    def run_json(self) -> dict:
        return json.loads((self.run_root / "evidence/run.json").read_text())

    def evidence_text(self) -> str:
        ev = self.run_root / "evidence"
        return "\n".join(p.read_text(errors="replace") for p in sorted(ev.rglob("*")) if p.is_file())

    def smv(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(SMV), *args], env=self.env, cwd=self.base, text=True,
                              capture_output=True, timeout=timeout)

    def py_argv(self, code: str, *args: str) -> list[str]:
        """argv that runs `code` with the module under test imported as `m`."""
        head = ("import importlib.util, json, sys\nfrom pathlib import Path\n"
                f"sys.path.insert(0, {str(SMV.parent)!r})\n"
                f"spec = importlib.util.spec_from_file_location('smv_under_test', {str(SMV)!r})\n"
                "m = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(m)\n")
        return [sys.executable, "-B", "-c", head + code, *args]

    def py(self, code: str, *args: str) -> subprocess.CompletedProcess:
        """Run `code` with the module under test as `m` inside this env (HOME, config dir, stubs)."""
        return subprocess.run(self.py_argv(code, *args), env=self.env, cwd=self.base, text=True,
                              capture_output=True, timeout=120)
