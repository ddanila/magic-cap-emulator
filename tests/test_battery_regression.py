import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "battery_regression.py"
SPEC = importlib.util.spec_from_file_location("battery_regression", MODULE_PATH)
assert SPEC and SPEC.loader
battery = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = battery
SPEC.loader.exec_module(battery)


class ConfigTests(unittest.TestCase):
    def test_healthy_selects_the_default_settings(self) -> None:
        config = battery.config_xml("datarover840d", battery.HEALTHY)

        self.assertIn('mask="3" defvalue="0" value="0"', config)
        self.assertIn('mask="12" defvalue="0" value="0"', config)

    def test_backup_empty_sets_only_the_backup_field(self) -> None:
        config = battery.config_xml("datarover840d", battery.BACKUP_EMPTY)

        self.assertIn('mask="3" defvalue="0" value="0"', config)
        self.assertIn('mask="12" defvalue="0" value="8"', config)

    def test_config_names_the_requested_system(self) -> None:
        self.assertIn(
            'name="datarover840"', battery.config_xml("datarover840", 0)
        )


class ScriptTests(unittest.TestCase):
    def test_script_calibrates_before_measuring(self) -> None:
        script = battery.automation_script("healthy.png")

        for target in ("press(240, 160)", "press(23, 23)", "press(456, 296)"):
            self.assertIn(target, script)
        self.assertIn("BATTERY_CHECKPOINT", script)
        self.assertIn("healthy.png", script)


class CheckpointTests(unittest.TestCase):
    def test_parses_both_checkpoints(self) -> None:
        output = (
            b"BATTERY_CHECKPOINT WHEN=before SCREEN=54C0E76A\n"
            b"BATTERY_COVER_REMOVED\n"
            b"BATTERY_CHECKPOINT WHEN=after SCREEN=58C0E76A\n"
        )

        points = battery.parse_checkpoints(output)

        self.assertEqual(points["before"], 0x54C0E76A)
        self.assertEqual(points["after"], 0x58C0E76A)

    def test_missing_checkpoints_yield_nothing(self) -> None:
        self.assertEqual(battery.parse_checkpoints(b"Average speed: 300%\n"), {})


class CoverScriptTests(unittest.TestCase):
    def test_cover_run_throws_the_switch_between_checkpoints(self) -> None:
        script = battery.automation_script("x.png", remove_cover=True)

        self.assertIn(f"frames == {battery.COVER_TOGGLE_FRAME} and true", script)
        self.assertIn(f":field({battery.COVER_REMOVED})", script)
        # The toggle has to land after the first checkpoint and before the
        # second, or the comparison proves nothing.  Anchor on the cover field
        # itself: touch presses call set_value too.
        toggle = script.index(f":field({battery.COVER_REMOVED}):set_value")
        self.assertLess(script.index("WHEN=before"), toggle)
        self.assertLess(toggle, script.index("WHEN=after"))

    def test_plain_run_never_touches_the_cover(self) -> None:
        script = battery.automation_script("x.png")

        self.assertIn(f"frames == {battery.COVER_TOGGLE_FRAME} and false", script)


if __name__ == "__main__":
    unittest.main()
