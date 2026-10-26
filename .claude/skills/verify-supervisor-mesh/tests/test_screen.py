"""Screen parsing on redacted captures from live run #4 (fixtures/) and synthetic frames; plus a scope-pick
drive against the stub tmux where the highlight moves after Down."""
import re
import unittest

from harness import RULE, TESTS, Env, load_smv, ready_screen

m = load_smv()
FIX = TESTS / "fixtures"


def fixture(name: str) -> str:
    return (FIX / name).read_text()


PERMISSION = ["╭" + "─" * 58 + "╮", "│ Bash command                                             │",
              "│   cat registry.json                                      │",
              "│ Do you want to proceed?                                  │",
              "│ ❯ 1. Yes                                                 │",
              "│   2. No, and tell Claude what to do differently          │", "╰" + "─" * 58 + "╯"]


class ScreenFixtures(unittest.TestCase):
    def test_scope_menu_capture(self):
        text = fixture("scope-menu.txt")
        self.assertEqual(m.screen_state(text), "scope-menu")
        options = [t for _, _, t in m.menu_options(text)]
        self.assertEqual(options.count(m.SCOPE_OPTION), 1)
        for want in ("Install for you (user scope)", "Install for you, in this repo only (local scope)",
                     "Open homepage", "Back to plugin list"):
            self.assertIn(want, options)
        self.assertEqual(m.highlighted(text)[1], "Install for you (user scope)")
        self.assertNotIn("/reload-plugins", " ".join(options), "the earlier input line is not in the menu block")

    def test_after_down_frame(self):
        self.assertEqual(m.highlighted(fixture("scope-menu-after-down.txt"))[1], m.SCOPE_OPTION)

    def test_menu_without_header_uses_the_rule_and_needs_the_footer(self):
        menu = ["older output"] * 14 + ["❯ /plugin install supervisor-mesh@supervisor-mesh", "▔" * 40,
                                        "  ❯ Install for you (user scope)", f"    {m.SCOPE_OPTION}",
                                        "    Install for you, in this repo only (local scope)", "    Open homepage",
                                        "", "   Enter to select · Esc to go back", "", ""]
        self.assertEqual(len(menu), 24)
        self.assertEqual(m.highlighted("\n".join(menu))[1], "Install for you (user scope)")
        self.assertIsNone(m.highlighted("\n".join(menu[:-3])), "no footer, no menu block, nothing highlighted")

    def test_trust_capture(self):
        text = fixture("trust.txt")
        self.assertEqual(m.screen_state(text), "trust")

    def test_ready_capture_auto_mode(self):
        text = fixture("ready-auto-mode.txt")
        self.assertEqual(m.screen_state(text), "ready")
        self.assertEqual(m.permission_mode(text), "auto mode")
        self.assertTrue(m.box_is_empty(m.input_box(text.splitlines())))

    def test_turn_done_capture(self):
        self.assertEqual(m.screen_state(fixture("turn-done.txt")), "ready")

    def test_y20_earlier_box_above_a_dialog_is_not_ready(self):
        text = "\n".join([RULE, "❯ /supervisor-mesh:init python", RULE, "", *PERMISSION])
        self.assertEqual(m.screen_state(text), "permission")

    def test_y20_box_with_dialog_text_below_is_not_ready(self):
        text = "\n".join(["reply", RULE, "❯ ", RULE, "  Do you want to make this edit to AGENTS.md?"])
        self.assertEqual(m.screen_state(text), "permission")

    def test_y20_box_far_above_the_bottom_does_not_count(self):
        text = "\n".join([RULE, "❯ ", RULE, *[f"output line {i}" for i in range(20)]])
        self.assertEqual(m.screen_state(text), "unknown")


class KeepLines(unittest.TestCase):
    def test_y10_ready_capture_keeps_box_footer_and_done_line_only(self):
        text = fixture("turn-done.txt")
        kept = [line for _, line in m.keep_lines(text, "state")]
        self.assertTrue(any(line.startswith("✻ Brewed") for line in kept))
        self.assertTrue(any("auto mode on" in line for line in kept))
        self.assertFalse(any("Init is done" in line for line in kept), "the reply area is never kept")

    def test_y10_tool_output_preview_is_never_kept(self):
        text = ready_screen("⏺ Bash(cat registry.json)", '  ⎿  {"plugins": {"SMV-MARKER-PREVIEW-x": []}}')
        self.assertFalse(any("SMV-MARKER" in line for _, line in m.keep_lines(text.replace("{box}", ""))))

    def test_y10_menu_and_dialog_kinds(self):
        menu = [line for _, line in m.keep_lines(fixture("scope-menu.txt"), "menu")]
        self.assertTrue(any(m.SCOPE_OPTION in line for line in menu))
        self.assertTrue(any("Enter to select" in line for line in menu))
        self.assertFalse(any("/reload-plugins" in line for line in menu))
        trust = [line for _, line in m.keep_lines(fixture("trust.txt"), "dialog")]
        self.assertTrue(any("Yes, I trust this folder" in line for line in trust))

    def test_y09_wrapped_address_drops_every_line_it_touches(self):
        pats = {"email-any": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")}
        lines = ["  first line", "  mail smv.wrap@exa", "  mple.com today", "  last line"]
        self.assertEqual(sorted(m.drop_map(lines, pats)), [1, 2])

    def test_box_holds_wrapped_text(self):
        self.assertTrue(m.box_holds("/supervisor-mesh:dispatch qa Verification order. Change no files and st art",
                                    "/supervisor-mesh:dispatch qa Verification order. Change no files and start"))
        self.assertFalse(m.box_holds("/supervisor-mesh:init", "/supervisor-mesh:init python"))


class ScopePickDrive(unittest.TestCase):
    """send + wait + scope-pick against the stub tmux: the menu opens on Enter, Down moves the highlight."""

    def test_scope_pick_moves_verifies_and_enters(self):
        e = Env(self.addCleanup)
        e.make_run()
        pane = e.pane()
        pane["screens"].update(menu=fixture("scope-menu.txt"), after_down=fixture("scope-menu-after-down.txt"))
        pane["on_enter"] = ["screen:menu", "submit-screen:main"]
        pane["on_key"] = {"Down": ["screen:after_down"]}
        e.save()
        r = e.smv("send", "--text", m.INSTALL_LINE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = e.smv("wait", "--timeout", "20", "--poll", "0.2")
        self.assertEqual(r.returncode, 12, r.stdout + r.stderr)
        r = e.smv("scope-pick", "--timeout", "20")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(e.keys_sent(), [["-l", m.INSTALL_LINE], ["Enter"], ["Down"], ["Enter"]])
        log = (e.run_root / "evidence/sends.log").read_text()
        self.assertEqual([x.split("\t")[3] for x in log.splitlines()],
                         [f"TYPE {m.INSTALL_LINE}", "KEY Enter", "KEY Down", "KEY Enter"])
        self.assertEqual(e.run_json()["scope_pick"]["result"], "ENTER")


if __name__ == "__main__":
    unittest.main()
