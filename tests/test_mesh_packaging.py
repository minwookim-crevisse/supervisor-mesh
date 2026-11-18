"""WO-0022 step 4 (W3): packaging and docs. `.mcp.json` at the plugin root launches the stdio server; the init and
dispatch skills point to the MCP tools and keep the CLI fallback; the README has the MCP section, the Python 3.11
floor and the `--label role=pm` PM pane instruction."""

import json
import re
import sys
import unittest

from _helpers import ROOT

sys.path.insert(0, str(ROOT))
from mesh import tools  # noqa: E402


class McpJsonTests(unittest.TestCase):
    def test_the_plugin_root_declares_the_stdio_server(self):
        data = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(
            data,
            {
                "mcpServers": {
                    "supervisor-mesh": {"command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/mesh/mcp_server.py"]}
                }
            },
        )
        self.assertTrue((ROOT / "mesh" / "mcp_server.py").is_file())


class SkillDocTests(unittest.TestCase):
    def text(self, name):
        return (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")

    def test_dispatch_points_to_work_dispatch_and_keeps_the_cli_fallback(self):
        text = self.text("dispatch")
        for kept in ("Do NOT auto-spawn", "projmux agent message send", "prepended verbatim"):
            self.assertIn(kept, text)
        self.assertIn("work_dispatch", text)
        self.assertIn("mcp__plugin_supervisor-mesh_supervisor-mesh__work_dispatch", text)
        self.assertRegex(text, r"(?i)fallback")

    def test_init_points_to_mesh_init_and_keeps_the_wo_0021_rules(self):
        text = self.text("init")
        self.assertIn("mesh_init", text)
        self.assertIn("plan_id", text)
        self.assertIn("--label role=pm", text)
        self.assertIn("companions_install", text)
        self.assertIn("skills/init/scaffold.py", text)  # the CLI fallback

    def test_every_mcp_tool_named_in_a_skill_exists(self):
        for name in ("init", "dispatch"):
            for tool in re.findall(r"mcp__plugin_supervisor-mesh_supervisor-mesh__(\w+)", self.text(name)):
                self.assertIn(tool, tools.REGISTRY, (name, tool))


class ReadmeTests(unittest.TestCase):
    def test_mcp_section_python_floor_and_pm_label(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^## MCP server")
        self.assertIn("python3 ≥ 3.11", text)
        self.assertNotIn("python3 ≥ 3.10", text)
        self.assertIn("--label role=pm", text)
        for name in tools.REGISTRY:
            self.assertIn(f"`{name}`", text, name)


if __name__ == "__main__":
    unittest.main()
