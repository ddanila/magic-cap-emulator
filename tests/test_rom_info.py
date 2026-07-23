import importlib.util
import io
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "rom_info.py"
SPEC = importlib.util.spec_from_file_location("rom_info", MODULE_PATH)
assert SPEC and SPEC.loader
rom_info = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rom_info
SPEC.loader.exec_module(rom_info)


def sample_rom() -> bytes:
    return (
        struct.pack(">I", rom_info.RESET_INSTRUCTION)
        + bytes(8)
        + rom_info.MONITOR_MARKER
        + b"synthetic test payload"
    )


def sample_flasher(payload: bytes) -> bytes:
    header = (
        rom_info.FLASH_MAGIC
        + struct.pack(">IIII", 1, 0, rom_info.FLASH_BASE, len(payload))
        + bytes(rom_info.FLASH_HEADER_SIZE - 0x1C)
    )
    return header + payload + bytes(
        [0xFF] * (rom_info.FLASH_CARD_SIZE - len(header) - len(payload))
    )


class RomInfoTests(unittest.TestCase):
    def test_inspects_big_endian_monitor_image(self) -> None:
        info = rom_info.inspect_rom(sample_rom())

        self.assertEqual(info.first_instruction, rom_info.RESET_INSTRUCTION)
        self.assertTrue(info.has_monitor_marker)
        self.assertEqual(info.size, len(sample_rom()))

    def test_rejects_too_short_rom(self) -> None:
        with self.assertRaisesRegex(rom_info.FormatError, "too short"):
            rom_info.inspect_rom(b"\x08\xf0")

    def test_parses_flasher_wrapper(self) -> None:
        payload = sample_rom()
        info = rom_info.inspect_flasher(sample_flasher(payload))

        self.assertEqual(info.version, 1)
        self.assertEqual(info.reserved, 0)
        self.assertEqual(info.base_address, rom_info.FLASH_BASE)
        self.assertEqual(info.payload, payload)
        self.assertTrue(info.header_is_zero_filled)
        self.assertTrue(info.padding_is_erased)

    def test_detects_non_erased_flasher_padding(self) -> None:
        card = bytearray(sample_flasher(sample_rom()))
        card[-1] = 0

        info = rom_info.inspect_flasher(bytes(card))

        self.assertFalse(info.padding_is_erased)

    def test_cli_accepts_matching_rom_and_flasher(self) -> None:
        payload = sample_rom()
        with tempfile.TemporaryDirectory() as directory:
            rom_path = Path(directory) / "test.image"
            card_path = Path(directory) / "card.bin"
            rom_path.write_bytes(payload)
            card_path.write_bytes(sample_flasher(payload))

            with redirect_stdout(io.StringIO()):
                result = rom_info.main(
                    [str(rom_path), "--flasher", str(card_path)]
                )

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
