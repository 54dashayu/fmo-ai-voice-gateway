import unittest
from unittest import mock

import checkin_details


class CheckinDetailsTests(unittest.TestCase):
    def test_direct_callsign_from_spaced_asr(self):
        self.assertEqual(checkin_details.callsign_from_asr("这里是 B H 一 J S S 报到"), "BH1JSS")

    def test_callsign_from_icao_spelling_words(self):
        self.assertEqual(
            checkin_details.callsign_from_asr("Bravo Hotel One Juliett Sierra Sierra"),
            "BH1JSS",
        )

    def test_invalid_asr_does_not_form_callsign(self):
        self.assertEqual(checkin_details.callsign_from_asr("北京昌平设备五瓦"), "")

    def test_mqtt_callsign_is_authoritative_over_conflicting_asr(self):
        mqtt_callsign = checkin_details.callsign_from_mqtt("BH1OFJ")
        asr_callsign = checkin_details.callsign_from_asr("这里是BH1JSS")
        self.assertEqual(mqtt_callsign, "BH1OFJ")
        self.assertEqual(asr_callsign, "BH1JSS")
        # The monitor uses mqtt_callsign directly and never selects asr_callsign.
        self.assertNotEqual(mqtt_callsign, asr_callsign)

    def test_power_levels_are_normalized(self):
        self.assertEqual(checkin_details.normalize_power("高功率"), "H")
        self.assertEqual(checkin_details.normalize_power("中M"), "M")
        self.assertEqual(checkin_details.normalize_power("低功率"), "L")

    def test_numeric_power_is_normalized_to_watts(self):
        self.assertEqual(checkin_details.normalize_power("五个瓦特"), "5W")
        self.assertEqual(checkin_details.normalize_power("二十瓦"), "20W")
        self.assertEqual(checkin_details.normalize_power("5 w"), "5W")

    def test_explicit_details_are_extracted_without_model(self):
        self.assertEqual(
            checkin_details.extract_explicit("使用设备全盛UVK六，QTH北京市昌平区，低功率发射。"),
            {"qth": "北京市昌平区", "device": "全盛UVK六", "power": "L"},
        )
        self.assertEqual(
            checkin_details.extract_explicit("QTH 北京市昌平区，使用设备全盛 UV-K 六，低功率发射。"),
            {"qth": "北京市昌平区", "device": "全盛 UV-K 六", "power": "L"},
        )

    def test_model_failure_falls_back_to_explicit_details(self):
        text = "我的QTH是北京市昌平区，使用设备是八通舟的二DR，低功率发射。"
        with mock.patch.object(checkin_details.gateway, "_post_json", side_effect=RuntimeError("upstream failed")):
            self.assertEqual(
                checkin_details.extract(text, {}),
                {"qth": "北京市昌平区", "device": "八通舟的二DR", "power": "L"},
            )


if __name__ == "__main__":
    unittest.main()
