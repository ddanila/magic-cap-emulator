from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import storage_translation_regression


class StorageTranslationRegressionTests(unittest.TestCase):
    def test_checkpoint_parser(self) -> None:
        output = (
            b"STORAGE_TRANSLATION ACCEPTED=00000001 FAILURES=00000000 "
            b"RESETS=00000000 CIS=A0 VERSION=00010001 TYPE=52414D43 "
            b"COMMON=00095FB8\n"
        )
        self.assertEqual(
            storage_translation_regression.parse_checkpoint(output),
            {
                "ACCEPTED": 1,
                "FAILURES": 0,
                "RESETS": 0,
                "CIS": 0xA0,
                "VERSION": 0x0001_0001,
                "TYPE": 0x5241_4D43,
                "COMMON": 0x0009_5FB8,
            },
        )

    def test_script_drives_real_translation_entry(self) -> None:
        script = storage_translation_regression.automation_script("/tmp/card")
        self.assertIn('load_card("card1", "/tmp/card")', script)
        self.assertIn("translation-prompt.png", script)
        self.assertIn("translation-selection.png", script)
        self.assertIn("translation-notebook-page2.png", script)
        self.assertIn("R17!=0", script)
        self.assertIn("RESETS=%08X", script)
        self.assertIn("STORAGE_TRANSLATION ACCEPTED=", script)

    def test_machine_config_selects_magicbus_keyboard(self) -> None:
        config = storage_translation_regression.deterministic_machine_config()
        self.assertIn('tag=":MAGICBUS_ACCESSORY"', config)
        self.assertIn('value="1"', config)


if __name__ == "__main__":
    unittest.main()
