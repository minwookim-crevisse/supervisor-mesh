import unittest

from _helpers import ROOT, frontmatter, invariant_block

SKILLS = {"init", "spawn-roles", "dispatch", "add-agent", "gc", "slack-setup"}
LEADS = {"fe", "qa", "designer", "backend", "security", "ship"}
DISCIPLINE = ("Operating model", "Karpathy 4", "Evidence-first gate")


class SkillTests(unittest.TestCase):
    def test_all_skills_present_with_frontmatter(self):
        found = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
        self.assertEqual(found, SKILLS)
        for name in SKILLS:
            fm = frontmatter(ROOT / "skills" / name / "SKILL.md")
            self.assertEqual(fm.get("name"), name)
            self.assertTrue(fm.get("description"), f"{name}: description missing")

    def test_side_effect_skills_are_user_invoked_only(self):
        for name in SKILLS - {"dispatch"}:
            fm = frontmatter(ROOT / "skills" / name / "SKILL.md")
            self.assertEqual(fm.get("disable-model-invocation"), "true", name)

    def test_skills_answer_in_operator_language(self):
        for name in SKILLS:
            self.assertIn("user's language", (ROOT / "skills" / name / "SKILL.md").read_text())

    def test_dispatch_never_auto_spawns(self):
        text = (ROOT / "skills/dispatch/SKILL.md").read_text()
        self.assertIn("Do NOT auto-spawn", text)
        self.assertIn("projmux agent message send", text)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.template = (ROOT / "templates/agents/role-lead.md").read_text()

    def test_base_set(self):
        names = {p.stem for p in (ROOT / "agents").glob("*.md")}
        self.assertEqual(names, {"pm-orchestrator", "scope-mapper"} | {f"role-lead-{r}" for r in LEADS})
        for p in (ROOT / "agents").glob("*.md"):
            fm = frontmatter(p)
            self.assertEqual(fm.get("name"), p.stem)
            self.assertTrue(fm.get("description"))

    def test_role_leads_inherit_invariant_block_verbatim(self):
        inv = invariant_block(self.template)
        for role in LEADS:
            text = (ROOT / "agents" / f"role-lead-{role}.md").read_text()
            self.assertEqual(invariant_block(text), inv, f"role-lead-{role} drifted from the template")
            self.assertNotIn("<ROLE>", text)
            self.assertIn("## Role scope", text)

    def test_template_keeps_placeholders(self):
        fm = frontmatter(ROOT / "templates/agents/role-lead.md")
        self.assertEqual(fm.get("name"), "role-lead-<role>")
        for section in DISCIPLINE:
            self.assertIn(section, self.template)

    def test_pm_embeds_discipline(self):
        text = (ROOT / "agents/pm-orchestrator.md").read_text()
        for section in DISCIPLINE:
            self.assertIn(section, text)

    def test_scope_mapper_is_read_only(self):
        fm = frontmatter(ROOT / "agents/scope-mapper.md")
        self.assertEqual(fm.get("tools"), "Read, Grep, Glob")


if __name__ == "__main__":
    unittest.main()
