from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import storage_card_regression


class StorageCardRegressionTests(unittest.TestCase):
    def test_checkpoint_parser(self) -> None:
        output = (
            b"STORAGE_BLANK CODE=A0 MAGIC=474D4D43 COMMON=FFFFFFFF\n"
            b"STORAGE_FORMAT HEADER=4D434150 CLUSTER=000000B0\n"
        )
        self.assertEqual(
            storage_card_regression.parse_checkpoints(output),
            {
                "BLANK": {
                    "CODE": 0xA0,
                    "MAGIC": 0x474D4D43,
                    "COMMON": 0xFFFFFFFF,
                },
                "FORMAT": {
                    "HEADER": 0x4D434150,
                    "CLUSTER": 0xB0,
                },
            },
        )

    def test_script_covers_product_lifecycle(self) -> None:
        setup = storage_card_regression.automation_script("/tmp/card")
        reinsert = storage_card_regression.reinsertion_script("/tmp/card")
        option = storage_card_regression.option_insert_script("/tmp/card")
        self.assertIn('card_image:load("/tmp/card")', setup)
        self.assertIn("STORAGE_BLANK", setup)
        self.assertIn("STORAGE_FORMAT", setup)
        self.assertIn('image:load("/tmp/card")', reinsert)
        self.assertIn("STORAGE_REINSERT", reinsert)
        self.assertIn("option_button:set_value(1)", option)
        self.assertIn("STORAGE_OPTION", option)
        self.assertIn("STORAGE_FINAL", option)

    def test_blank_digest_is_stable(self) -> None:
        self.assertEqual(
            storage_card_regression.BLANK_SHA256,
            "9f9b02f5ee6cbef5e018c1ee424095fc21a842ea6968c0d36114b5930dab2ba1",
        )


if __name__ == "__main__":
    unittest.main()
