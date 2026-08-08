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

    def test_ignores_status_and_non_fmo_packets(self):
        self.assertIsNone(
            aprs_positions.parse_position_packet(
                "BG6AGC-5>APFMO2,TCPIP*:>正在某服务器上守听"
            )
        )
        self.assertIsNone(
            aprs_positions.parse_position_packet(
                "BG6AGC-5>APRS,TCPIP*:=2943.63NF11818.24Ei"
            )
        )


if __name__ == "__main__":
    unittest.main()
