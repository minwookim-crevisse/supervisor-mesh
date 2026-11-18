"""WO-0022 step 4 (W4): D89 goal intake. `/supervisor-mesh:run <goal>` loads the PM procedure (decompose one goal into
work items with depends, write the orders, ready, dispatch in dependency order over projmux messages, gate each on
mesh-run evidence, report with mesh_status / mesh_metrics); `agents/pm-orchestrator.md` states it. Every MCP tool a
skill names exists in `tools.REGISTRY`."""

import re
import sys
import unittest

from _helpers import ROOT, frontmatter

sys.path.insert(0, str(ROOT))
from mesh import tools  # noqa: E402

PREFIX = "mcp__plugin_supervisor-mesh_supervisor-mesh__"
# a bare tool-shaped name in backticks (`work_new`, `mesh_status`); `mesh_init`-style words only, not paths or flags
BARE = re.compile(r"`((?:mesh|work|pane|tracker|companions)_[a-z_]+)`")


def skill(name: str) -> str:
    return (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


class ToolNamesTests(unittest.TestCase):
    def test_every_mcp_tool_named_in_run_dispatch_and_init_exists(self):
        for name in ("run", "dispatch", "init"):
            text = skill(name)
            named = set(re.findall(re.escape(PREFIX) + r"(\w+)", text)) | set(BARE.findall(text))
            self.assertTrue(named, name)
            for tool in sorted(named):
                self.assertIn(tool, tools.REGISTRY, (name, tool))

    def test_the_run_skill_uses_the_full_tool_name_form_for_every_tool_it_calls(self):
        text = skill("run")
        full = set(re.findall(re.escape(PREFIX) + r"(\w+)", text))
        for tool in ("work_new", "work_transition", "work_dispatch", "work_show", "mesh_status", "mesh_metrics"):
            self.assertIn(tool, full, tool)


class RunSkillTests(unittest.TestCase):
    def test_frontmatter(self):
        fm = frontmatter(ROOT / "skills" / "run" / "SKILL.md")
        self.assertEqual(fm.get("name"), "run")
        self.assertTrue(fm.get("description"))
        self.assertEqual(fm.get("disable-model-invocation"), "true")
        self.assertTrue(fm.get("argument-hint"))
        allowed = fm.get("allowed-tools", "").split()
        self.assertTrue(allowed)
        scoped = ("Read", "Write(docs/work-orders/**)", "Edit(docs/work-orders/**)")  # F13: never unscoped Write/Edit
        self.assertTrue(all(a.startswith(PREFIX) or a in scoped for a in allowed), allowed)
        self.assertNotIn("Bash", fm.get("allowed-tools", ""))  # no send-keys path, no shelling out

    def test_the_procedure(self):
        text = skill("run")
        self.assertIn("user's language", text)
        self.assertIn("$ARGUMENTS", text)
        for must in ("depends", "ready", "dependency order", "never send-keys", "submit", "verify", "operator never"):
            self.assertIn(must, text, must)
        self.assertRegex(text, r"(?i)pane_command`?\s.*only.*slash command")
        self.assertRegex(text, r"(?i)prose report")

    def test_the_pm_agent_states_it_and_keeps_its_discipline(self):
        text = (ROOT / "agents" / "pm-orchestrator.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^## Goal intake")
        self.assertIn("/supervisor-mesh:run", text)
        for must in ("work_new", "work_dispatch", "mesh_metrics", "dependency order"):
            self.assertIn(must, text, must)
        self.assertIn("## Operating model (INVARIANT)", text)


if __name__ == "__main__":
    unittest.main()
