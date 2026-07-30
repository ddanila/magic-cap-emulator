from __future__ import annotations

import unittest
from pathlib import Path

from tools.uart_dma_regression import (
    DmaResult,
    automation_script,
    parse_results,
    verify_results,
)


class UartDmaRegressionTests(unittest.TestCase):
    def test_parses_both_dma_phases(self) -> None:
        output = (
            b"UART_DMA PHASE=1 A_CTL=C0000001 A_COUNT=00000003 "
            b"B_CTL=C0000001 B_COUNT=00000003 "
            b"IRQ=00C03000 RX=42525823\n"
            b"UART_DMA PHASE=2 A_CTL=C0000001 A_COUNT=00000003 "
            b"B_CTL=C0000001 B_COUNT=00000003 "
            b"IRQ=00C03000 RX=41525824\n"
        )
        results = parse_results(output)
        self.assertEqual(results[1].received, 0x42525823)
        self.assertEqual(results[2].interrupt, 0x00C03000)

    def test_accepts_complete_register_and_transport_contract(self) -> None:
        results = {
            1: DmaResult(
                1,
                0xC0000001,
                3,
                0xC0000001,
                3,
                0x00C03000,
                0x42525823,
            ),
            2: DmaResult(
                2,
                0xC0000001,
                3,
                0xC0000001,
                3,
                0x00C03000,
                0x41525824,
            ),
        }
        self.assertEqual(
            verify_results(results, {1: b"ATX!", 2: b"BTX?"}),
            [],
        )

    def test_rejects_missing_and_wrong_dma_state(self) -> None:
        bad = DmaResult(1, 1, 2, 1, 4, 0, 0)
        failures = verify_results({1: bad}, {1: b"", 2: b""})
        self.assertTrue(any("UART A control" in item for item in failures))
        self.assertTrue(any("counts" in item for item in failures))
        self.assertTrue(any("phase 2 report is missing" in item for item in failures))

    def test_script_uses_both_connectors_and_hardware_count(self) -> None:
        script = automation_script(
            (
                Path("/tmp/ready1"),
                Path("/tmp/sent1"),
                Path("/tmp/ready2"),
                Path("/tmp/sent2"),
            )
        )
        self.assertIn("DINO + UART_A + 0x10", script)
        self.assertIn("DINO + UART_B + 0x10", script)
        self.assertIn("DMA_RX", script)
        self.assertIn("DMA_TX", script)


if __name__ == "__main__":
    unittest.main()
