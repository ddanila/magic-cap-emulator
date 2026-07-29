import unittest

from tools.product_data_modem_regression import (
    HALF_DMA_BYTES,
    MIN_PCM_BYTES,
    PRODUCT_SYMBOLS,
    answer_automation_script,
    async_ppp_frame,
    echo_responder_words,
    final_ipcp_response,
    internet_checksum,
    initial_lcp_response,
    initial_ipcp_response,
    machine_config,
    parse_echo_result,
    parse_peer_data,
    parse_product_result,
    ppp_fcs,
    product_automation_script,
    tcp_syn_ack_response,
    validate_results,
)


class ProductDataModemScriptTests(unittest.TestCase):
    def test_script_drives_browser_provider_and_reload(self) -> None:
        script = product_automation_script()

        self.assertIn("Internet Center -> Downtown -> Hallway -> Desk", script)
        self.assertIn("press(225, 225)", script)
        self.assertIn("press(170, 153)", script)
        self.assertIn("press(450, 250)", script)
        self.assertIn('trigger_file:write("dialed', script)
        self.assertIn("program:read_u32(CALL_READY_COUNTER) > 0", script)
        self.assertIn(
            "frames >= call_ready_frame + CALL_SETTLE_FRAMES", script
        )
        self.assertNotIn("frames == 5100", script)
        self.assertIn("product.result-ready", script)
        self.assertIn("answer.result-ready", script)
        self.assertIn("PRODUCT_DATA_MODEM_RESULT", script)
        self.assertNotIn("0x13c29434", script)

    def test_configs_select_exchange_for_product_and_bridge_for_answer(self) -> None:
        self.assertIn('value="3"', machine_config("product"))
        self.assertIn('value="2"', machine_config("answer"))

    def test_answer_peer_wakes_before_direct_answer_injection(self) -> None:
        script = answer_automation_script()

        self.assertIn('ports[":POWER_BUTTON"]', script)
        self.assertIn("frames == 550", script)
        self.assertIn("frames == 700", script)
        self.assertIn("press(440, 10)", script)
        self.assertIn("frames == 999999", script)
        self.assertIn("if saved_state == nil", script)
        self.assertIn('io.open(call_trigger_path, "r")', script)
        self.assertIn("answer.result-ready", script)
        self.assertIn("product.result-ready", script)
        self.assertIn("protocol.result-ready", script)
        self.assertIn("PRODUCT_ANSWER_ECHO_START", script)
        self.assertIn("PRODUCT_ANSWER_PEER_DATA", script)
        self.assertIn("PRODUCT_ANSWER_ECHO bytes=", script)
        self.assertIn("PRODUCT_ANSWER_ECHO_DATA hex=", script)
        self.assertIn("PRODUCT_ANSWER_LCP_REPLY read=", script)
        self.assertIn("local ANSWER_DELIVER_COUNTER = 0x0030304c", script)

    def test_answer_echo_uses_rom_read_pending_read_and_write(self) -> None:
        words = echo_responder_words()

        self.assertIn(0x3739_2DDC, words)
        self.assertIn(0x3739_2CC8, words)
        self.assertIn(0x3739_2C80, words)
        self.assertEqual(words[-1], 0)
        self.assertEqual(words[-2], 0x1000_FFFF)

    def test_ppp_fcs_and_initial_lcp_response(self) -> None:
        request = bytes.fromhex(
            "ff03c021011f000e02060000000007020802"
        )
        self.assertEqual(ppp_fcs(request), 0x5E26)
        self.assertEqual(
            async_ppp_frame(request),
            bytes.fromhex(
                "7eff7d23c0217d217d3f7d207d2e7d227d267d207d207d207d20"
                "7d277d227d287d22265e7e"
            ),
        )
        response = initial_lcp_response()
        self.assertTrue(response.startswith(bytes.fromhex("7eff7d23c0217d22")))
        self.assertEqual(response.count(0x7E), 4)
        ipcp = initial_ipcp_response()
        self.assertIn(bytes.fromhex("7d2a7d207d227d2f"), ipcp)
        self.assertIn(bytes.fromhex("7d2a7d207d227d22"), ipcp)
        self.assertEqual(ipcp.count(0x7E), 4)
        final_ipcp = final_ipcp_response()
        self.assertIn(
            bytes.fromhex("7d2a7d207d227d2f7d227d267d202d7d237d21"),
            final_ipcp,
        )
        self.assertEqual(final_ipcp.count(0x7E), 2)
        self.assertEqual(internet_checksum(bytes.fromhex("0001f203f4f5")), 0x1905)
        syn_ack = tcp_syn_ack_response()
        self.assertIn(bytes.fromhex("7d2a7d207d227d22"), syn_ack)
        self.assertEqual(syn_ack.count(0x7E), 2)


class ProductDataModemResultTests(unittest.TestCase):
    PEER_IP_DATA = bytes.fromhex(
        "ff0300214500002c7c7300004006e648"
        "0a00020f0a00020204001f903f999b"
    )

    @staticmethod
    def product_output() -> bytes:
        counters = " ".join(
            f"{name}={5 if name == 'lcp_frame' else 4 if name == 'ppp_read' else 1}"
            for _, name in PRODUCT_SYMBOLS
        )
        return (
            f"PRODUCT_DATA_MODEM_RESULT {counters} "
            "detector=1 rates=FFF0,FFF0,FFF0,FFF0 enables=3 size=48 "
            "initargs=00000001,00000002,00000003,00000004,"
            "00000005,00000006,00000007 "
            "cfg=00000008,00000009,0000000A,0000000B "
            "status=00402526,00000026,00000000,00000000 "
            "status_caller=13E50000 status_target=13E43384\n"
        ).encode()

    @staticmethod
    def answer_result() -> dict[str, int | str]:
        return {
            "role": "answer",
            "open": 1,
            "init": 1,
            "receive": 1,
            "transmit": 1,
            "install": 1,
            "pump": 1,
            "report_status": 1,
            "report_signal": 1,
            "framer_hdlc_init": 1,
            "lapm_init": 1,
            "lapm_start": 1,
            "lapm_main": 1,
            "lapm_report_connect": 1,
            "lapm_process_sabme": 1,
            "lapm_deliver_data": 4,
            "status_callback": 1,
            "data_mode": 1,
            "returned": 1,
            "detector": 1,
            "rates": (0xFFF0,) * 4,
            "enables": 3,
            "size": 48,
        }

    def test_result_parses_and_complete_pair_is_accepted(self) -> None:
        product = parse_product_result(self.product_output())

        self.assertIsNotNone(product)
        assert product is not None
        self.assertEqual(product["connect_number"], 1)
        self.assertEqual(product["rates"], (0xFFF0,) * 4)
        self.assertEqual(product["initargs"], tuple(range(1, 8)))
        self.assertEqual(product["config"], tuple(range(8, 12)))
        self.assertEqual(product["status"][:2], (0x00402526, 0x26))
        self.assertEqual(
            validate_results(
                product,
                self.answer_result(),
                [MIN_PCM_BYTES, MIN_PCM_BYTES + HALF_DMA_BYTES],
                len(initial_lcp_response())
                + len(initial_ipcp_response())
                + len(final_ipcp_response()),
                self.PEER_IP_DATA,
            ),
            [],
        )
        self.assertEqual(
            parse_echo_result(b"PRODUCT_ANSWER_ECHO bytes=37\n"), 37
        )

    def test_missing_product_data_mode_is_rejected(self) -> None:
        product = parse_product_result(self.product_output())
        assert product is not None
        product["data_mode"] = 0

        failures = validate_results(
            product,
            self.answer_result(),
            [MIN_PCM_BYTES, MIN_PCM_BYTES],
            len(initial_lcp_response())
            + len(initial_ipcp_response())
            + len(final_ipcp_response()),
            self.PEER_IP_DATA,
        )

        self.assertIn("product missed data_mode", failures)

    def test_missing_product_ppp_write_is_rejected(self) -> None:
        product = parse_product_result(self.product_output())
        assert product is not None
        product["ppp_write"] = 0

        failures = validate_results(
            product,
            self.answer_result(),
            [MIN_PCM_BYTES, MIN_PCM_BYTES],
            len(initial_lcp_response())
            + len(initial_ipcp_response())
            + len(final_ipcp_response()),
            self.PEER_IP_DATA,
        )

        self.assertIn("product missed ppp_write", failures)

    def test_transient_high_rate_flags_do_not_change_negotiated_payload(self) -> None:
        product = parse_product_result(self.product_output())
        assert product is not None
        product["rates"] = (0x8880, 0xFFF0, 0xFFF0, 0xFFF0)

        failures = validate_results(
            product,
            self.answer_result(),
            [MIN_PCM_BYTES, MIN_PCM_BYTES],
            len(initial_lcp_response())
            + len(initial_ipcp_response())
            + len(final_ipcp_response()),
            self.PEER_IP_DATA,
        )

        self.assertNotIn("the peers negotiated different rate words", failures)

    def test_stable_rate_payload_mismatch_is_rejected(self) -> None:
        product = parse_product_result(self.product_output())
        assert product is not None
        product["rates"] = (0xFFF0, 0xFFE0, 0xFFF0, 0xFFF0)

        failures = validate_results(
            product,
            self.answer_result(),
            [MIN_PCM_BYTES, MIN_PCM_BYTES],
            len(initial_lcp_response())
            + len(initial_ipcp_response())
            + len(final_ipcp_response()),
            self.PEER_IP_DATA,
        )

        self.assertIn("the peers negotiated different rate words", failures)

    def test_product_detector_may_clear_after_sticky_data_mode_entry(self) -> None:
        product = parse_product_result(self.product_output())
        assert product is not None
        product["detector"] = 0

        failures = validate_results(
            product,
            self.answer_result(),
            [MIN_PCM_BYTES, MIN_PCM_BYTES],
            len(initial_lcp_response())
            + len(initial_ipcp_response())
            + len(final_ipcp_response()),
            self.PEER_IP_DATA,
        )

        self.assertEqual(failures, [])

    def test_answer_detector_may_clear_after_first_ip_packet(self) -> None:
        product = parse_product_result(self.product_output())
        assert product is not None
        answer = self.answer_result()
        answer["detector"] = 0

        failures = validate_results(
            product,
            answer,
            [MIN_PCM_BYTES, MIN_PCM_BYTES],
            len(initial_lcp_response())
            + len(initial_ipcp_response())
            + len(final_ipcp_response()),
            self.PEER_IP_DATA,
        )

        self.assertEqual(failures, [])

    def test_missing_peer_replies_are_rejected(self) -> None:
        product = parse_product_result(self.product_output())
        assert product is not None

        failures = validate_results(
            product,
            self.answer_result(),
            [MIN_PCM_BYTES, MIN_PCM_BYTES],
            None,
            self.PEER_IP_DATA,
        )

        self.assertIn("answer did not report its PPP peer replies", failures)

    def test_final_answer_read_parses_first_ip_packet(self) -> None:
        output = (
            b"PRODUCT_ANSWER_ECHO_DATA hex="
            b"7EFF7D237D2021457D207D202C7C737D207D20407D26E648"
            b"7D2A7D207D227D2F7D2A7D207D227D227D247D207D3F903F999B\n"
        )

        self.assertEqual(parse_peer_data(output), self.PEER_IP_DATA)


if __name__ == "__main__":
    unittest.main()
