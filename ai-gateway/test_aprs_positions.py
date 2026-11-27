import unittest

import aprs_positions


class AprsPositionTests(unittest.TestCase):
    def test_parses_live_fmo_position(self):
        line = "BG6AGC-5>APFMO2,TCPIP*,qAC,T2FZ:=2943.63NF11818.24Ei大美黄山"
        self.assertEqual(
            aprs_positions.parse_position_packet(line, received_at=123),
            {
                "callsign": "BG6AGC-5",
                "latitude": 29.727167,
                "longitude": 118.304,
                "receivedAt": 123,
                "destination": "APFMO2",
                "source": "APRS-IS",
            },
        )

    def test_ignores_status_and_accepts_other_clients(self):
        self.assertIsNone(
            aprs_positions.parse_position_packet(
                "BG6AGC-5>APFMO2,TCPIP*:>正在某服务器上守听"
            )
        )
        self.assertIsNotNone(
            aprs_positions.parse_position_packet(
                "BG6AGC-5>APRS,TCPIP*:=2943.63NF11818.24Ei"
            )
        )

    def test_base_call_selects_latest_ssid_but_explicit_ssid_is_exact(self):
        saved = aprs_positions._positions
        try:
            aprs_positions._positions = {
                'BH1JSS': {'callsign': 'BH1JSS', 'receivedAt': 1},
                'BH1JSS-7': {'callsign': 'BH1JSS-7', 'receivedAt': 2},
                'BH1JSSX': {'callsign': 'BH1JSSX', 'receivedAt': 3}
            }
            self.assertEqual(aprs_positions.lookup('BH1JSS')['callsign'], 'BH1JSS-7')
            self.assertIsNone(aprs_positions.lookup('BH1JSS-9'))
        finally:
            aprs_positions._positions = saved

    def test_rejects_invalid_minutes(self):
        self.assertIsNone(aprs_positions.parse_position_packet('BH1JSS>APRS:=3960.00N/11600.00E>'))


if __name__ == "__main__":
    unittest.main()
