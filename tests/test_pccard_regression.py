import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "pccard_regression.py"
SPEC = importlib.util.spec_from_file_location("pccard_regression", MODULE_PATH)
assert SPEC and SPEC.loader
pccard_regression = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pccard_regression
SPEC.loader.exec_module(pccard_regression)


class PCCardRegressionTests(unittest.TestCase):
    def test_parses_checkpoint(self) -> None:
        output = (
            b"PCCARD_CHECKPOINT COMMON=426F7773 ATTR=01,03,61 "
            b"GLACIER=030E WRITE=13579BDF\n"
        )

        self.assertEqual(
            pccard_regression.parse_checkpoint(output),
            (0x426F7773, (0x01, 0x03, 0x61), 0x030E, 0x13579BDF),
        )

    def test_rejects_missing_checkpoint(self) -> None:
        self.assertIsNone(pccard_regression.parse_checkpoint(b"booting\n"))

    def test_parses_os_checkpoint(self) -> None:
        output = (
            b"PCCARD_OS_CHECKPOINT STATE=0001 "
            b"WORKBENCH=9DAB458B NONZERO=7077\n"
        )

        self.assertEqual(
            pccard_regression.parse_os_checkpoint(output),
            (0x0001, 0x9DAB458B, 7077),
        )

    def test_script_probes_both_spaces_and_write_path(self) -> None:
        script = pccard_regression.automation_script()

        self.assertIn("0x24000000", script)
        self.assertIn("0x08000000", script)
        self.assertIn("0x1040000c", script)
        self.assertIn("0x247ffffc", script)
        self.assertIn("image:load", script)
        self.assertIn("0x0000e7e0", script)

    def test_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample"
            path.write_bytes(b"Magic Cap")

            self.assertEqual(
                pccard_regression.sha256(path),
                hashlib.sha256(b"Magic Cap").hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
