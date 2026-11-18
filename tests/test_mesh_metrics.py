"""WO-0022 step 4 (W4): the D90 counters. `mesh_metrics` (any caller, read-only), `mesh_status` with `metrics: true`
and the CLI `mesh_status --metrics` print three counters taken from state.json item history and events.jsonl only:
entries into rework per item, PASS reversals (verified or closed -> rework) and operator interventions (operator events
recorded; receipts and hook_error lines never count). Indicative only, never a gate."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _helpers import ROOT
from _mesh_fakes import parse_caller
from test_mesh_state import doc_of, item_in

sys.path.insert(0, str(ROOT))
from mesh import tools  # noqa: E402
from mesh.model import Actor, HistoryEntry, Phase, Verb  # noqa: E402
from mesh.store import Store  # noqa: E402

P = Phase
AT = "2026-11-18T00:00:00Z"


def h(verb, frm, to, actor=Actor.PM, row="r"):
    return HistoryEntry(AT, verb, row, actor, None, frm, to)


# WO-0001 is READY now, but its history entered rework twice: once from verifying (a fail), once from verified (a
# reject: a PASS reversal). A count from the current state alone would say 0 and 0.
WO1 = (
    h(Verb.NEW, None, P.DRAFT),
    h(Verb.READY, P.DRAFT, P.READY),
    h(Verb.START, P.READY, P.RUNNING, Actor.LEAD_OWN),
    h(Verb.SUBMIT, P.RUNNING, P.VERIFYING, Actor.LEAD_OWN),
    h(Verb.FAIL, P.VERIFYING, P.REWORK, Actor.LEAD_OWN),
    h(Verb.READY, P.REWORK, P.READY),
    h(Verb.START, P.READY, P.RUNNING, Actor.LEAD_OWN),
    h(Verb.SUBMIT, P.RUNNING, P.VERIFYING, Actor.LEAD_OWN),
    h(Verb.MESH, P.VERIFYING, P.VERIFIED, Actor.MESH),
    h(Verb.REJECT, P.VERIFIED, P.REWORK),
    h(Verb.READY, P.REWORK, P.READY),
)
# WO-0002 is in REWORK after an operator override from closed (a PASS reversal); a pane_command receipt entry
# (verb None, rework -> rework) is not an entry into rework.
WO2 = (
    h(Verb.NEW, None, P.DRAFT),
    h(Verb.OVERRIDE, P.DRAFT, P.CLOSED, Actor.OPERATOR),
    h(Verb.OVERRIDE, P.CLOSED, P.REWORK, Actor.OPERATOR),
    h(None, P.REWORK, P.REWORK, row="pane_command"),
)
# WO-0003 never reworked.
WO3 = (h(Verb.NEW, None, P.DRAFT), h(Verb.READY, P.DRAFT, P.READY))

EXPECTED = {
    "rework": {"total": 3, "by_item": {"WO-0001": 2, "WO-0002": 1, "WO-0003": 0}},
    "pass_reversals": {"total": 2, "by_item": {"WO-0001": 1, "WO-0002": 1, "WO-0003": 0}},
    "operator_interventions": 3,
}


class MetricsCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "proj"
        self.root.mkdir()
        (self.tmp / "home").mkdir()
        self.store = Store(self.root / ".mesh")
        self.store.create()

    def tearDown(self):
        self._tmp.cleanup()

    def seed_history(self):
        with self.store.locked() as txn:
            txn.commit(
                doc_of(
                    item_in(P.READY, "WO-0001", history=WO1),
                    item_in(P.REWORK, "WO-0002", history=WO2),
                    item_in(P.READY, "WO-0003", history=WO3),
                )
            )
        for text in ("start the batch", "please run /compact", "companions_install"):
            self.store.record_operator_event(text, AT, "pm-uid")
        self.store.append_receipt({"id": "rc-1", "at": AT, "tool": "pane_command"})
        self.store.append_receipt({"id": "rc-2", "at": AT, "tool": "companions_install"})
        self.store.append_hook_error(AT, "ValueError")

    def ctx(self, caller="unknown"):
        env = {"HOME": str(self.tmp / "home"), "PATH": "/nonexistent"}
        who = parse_caller(caller)
        return tools.Context.for_project(self.root, env, clock=lambda: AT, caller=lambda: who)


class MetricsToolTests(MetricsCase):
    def test_counts_come_from_recorded_history_and_receipts_never_count(self):
        self.seed_history()
        out = tools.call(self.ctx(), "mesh_metrics", {})
        self.assertTrue(out.ok, out.data)
        self.assertEqual({k: out.data[k] for k in EXPECTED}, EXPECTED)
        self.assertRegex(out.data["note"], r"(?i)indicative only, never a gate")

    def test_any_caller_reads_and_nothing_is_written(self):
        self.seed_history()
        before = {p.name: p.read_bytes() for p in self.store.dir.iterdir() if p.is_file()}
        for caller in ("unknown", "pm:pm-uid", "lead:backend:be-uid"):
            self.assertTrue(tools.call(self.ctx(caller), "mesh_metrics", {}).ok, caller)
        after = {p.name: p.read_bytes() for p in self.store.dir.iterdir() if p.is_file()}
        self.assertEqual(after, before)
        self.assertTrue(tools.REGISTRY["mesh_metrics"].read_only)
        self.assertEqual(tools.REGISTRY["mesh_metrics"].access, "any")

    def test_an_empty_project_counts_zero(self):
        out = tools.call(self.ctx(), "mesh_metrics", {})
        self.assertEqual(
            {k: out.data[k] for k in EXPECTED},
            {
                "rework": {"total": 0, "by_item": {}},
                "pass_reversals": {"total": 0, "by_item": {}},
                "operator_interventions": 0,
            },
        )

    def test_an_uninitialised_project_says_to_run_mesh_init(self):
        self.store.state_path.unlink()
        out = tools.call(self.ctx(), "mesh_metrics", {})
        self.assertEqual((out.ok, out.data["error"]["code"]), (False, "not_initialized"))


class StatusMetricsTests(MetricsCase):
    def test_mesh_status_adds_the_counters_only_when_asked(self):
        self.seed_history()
        plain = tools.call(self.ctx(), "mesh_status", {})
        self.assertNotIn("metrics", plain.data)
        asked = tools.call(self.ctx(), "mesh_status", {"metrics": True})
        self.assertTrue(asked.ok, asked.data)
        self.assertEqual({k: asked.data["metrics"][k] for k in EXPECTED}, EXPECTED)
        self.assertEqual(asked.data["items"], plain.data["items"])

    def test_the_cli_flag_is_derived_from_the_registry(self):
        self.seed_history()
        env = {
            "HOME": str(self.tmp / "home"),
            "CLAUDE_PROJECT_DIR": str(self.root),
            "PATH": "/nonexistent",  # no projmux: the caller is unknown, and mesh_status still answers
        }
        cmd = [sys.executable, str(ROOT / "mesh" / "cli.py")]
        run = subprocess.run(
            [*cmd, "mesh_status", "--metrics"], capture_output=True, text=True, timeout=30, env=env, cwd=self.tmp
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        data = json.loads(run.stdout)
        self.assertEqual({k: data["metrics"][k] for k in EXPECTED}, EXPECTED)
        metrics = subprocess.run(
            [*cmd, "mesh_metrics"], capture_output=True, text=True, timeout=30, env=env, cwd=self.tmp
        )
        self.assertEqual(metrics.returncode, 0, metrics.stdout + metrics.stderr)
        self.assertEqual({k: json.loads(metrics.stdout)[k] for k in EXPECTED}, EXPECTED)


if __name__ == "__main__":
    unittest.main()
