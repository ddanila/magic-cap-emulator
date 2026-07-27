from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest


TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import patch_tls_browser as browser_patch  # noqa: E402


class PayloadTests(unittest.TestCase):
    def test_corrects_only_dispatch_literal(self) -> None:
        payload = b"prefix" + browser_patch.BROKEN_LITERAL + b"https://suffix"
        corrected = browser_patch.patch_payload(
            payload,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_size=len(payload),
            expected_offset=6,
        )

        self.assertEqual(
            b"prefix" + browser_patch.FIXED_LITERAL + b"https://suffix",
            corrected,
        )
        self.assertEqual(len(payload), len(corrected))

    def test_published_hashes_are_distinct(self) -> None:
        self.assertNotEqual(
            browser_patch.SOURCE_SHA256,
            browser_patch.PATCHED_SHA256,
        )

    def test_rejects_wrong_input_hash(self) -> None:
        payload = b"prefix" + browser_patch.BROKEN_LITERAL

        with self.assertRaisesRegex(browser_patch.PatchError, "SHA-256"):
            browser_patch.patch_payload(
                payload,
                expected_sha256="0" * 64,
                expected_size=len(payload),
                expected_offset=None,
            )

    def test_rejects_ambiguous_literal(self) -> None:
        payload = browser_patch.BROKEN_LITERAL * 2

        with self.assertRaisesRegex(browser_patch.PatchError, "found 2"):
            browser_patch.patch_payload(
                payload,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                expected_size=len(payload),
                expected_offset=None,
            )


class CommandTests(unittest.TestCase):
    def test_refuses_to_replace_output_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.pkg"
            output = directory / "output.pkg"
            source.write_bytes(b"not the package")
            output.write_bytes(b"keep")

            result = browser_patch.main([str(source), str(output)])

            self.assertEqual(2, result)
            self.assertEqual(b"keep", output.read_bytes())


if __name__ == "__main__":
    unittest.main()
