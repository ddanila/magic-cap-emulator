import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "menu_touch_regression.py"
SPEC = importlib.util.spec_from_file_location("menu_touch_regression", MODULE_PATH)
assert SPEC and SPEC.loader
menu_touch_regression = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = menu_touch_regression
SPEC.loader.exec_module(menu_touch_regression)


PASSING_TRACE = b"""\
MENU_TOUCH_BINDING x=GUNCODE_1_XAXIS y=GUNCODE_1_YAXIS button=GUNCODE_1_BUTTON1
MENU_TOUCH frame=1 menu=0 button=0 x=8000 y=8000
MENU_TOUCH frame=16 menu=0 button=1 x=5C93 y=5ADB
MENU_TOUCH frame=28 menu=0 button=0 x=5C93 y=5ADB
MENU_TOUCH frame=44 menu=1 button=0 x=5C93 y=5ADB
MENU_TOUCH frame=82 menu=0 button=0 x=5C93 y=5ADB
MENU_TOUCH frame=126 menu=0 button=0 x=9CA9 y=95A5
MENU_TOUCH frame=127 menu=0 button=1 x=9CA9 y=95A5
"""


class MenuTouchRegressionTests(unittest.TestCase):
    def test_accepts_single_gun_binding_and_post_menu_press(self) -> None:
        self.assertIsNone(menu_touch_regression.evaluate_trace(PASSING_TRACE))

    def test_rejects_old_split_mouse_binding(self) -> None:
        old_trace = PASSING_TRACE.replace(
            b"x=GUNCODE_1_XAXIS y=GUNCODE_1_YAXIS "
            b"button=GUNCODE_1_BUTTON1",
            b"x=GUNCODE_1_XAXIS y=GUNCODE_1_YAXIS "
            b"button=MOUSECODE_1_BUTTON1",
        )

        failure = menu_touch_regression.evaluate_trace(old_trace)

        self.assertIsNotNone(failure)
        self.assertIn("expected one lightgun device", failure)

    def test_rejects_stale_position_after_menu(self) -> None:
        stale_trace = PASSING_TRACE.replace(
            b"x=9CA9 y=95A5", b"x=5C93 y=5ADB"
        )

        self.assertEqual(
            menu_touch_regression.evaluate_trace(stale_trace),
            "the pen button recovered but its absolute position stayed stale",
        )

    def test_script_observes_real_menu_and_raw_ports(self) -> None:
        script = menu_touch_regression.automation_script()

        self.assertIn("ui.menu_active", script)
        self.assertIn('ports[":TOUCH_X"]', script)
        self.assertIn("MENU_TOUCH_BINDING", script)


if __name__ == "__main__":
    unittest.main()
