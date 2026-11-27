import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import net_checkin_runtime
import net_checkin_state


class NetCheckinRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.runtime_patch = patch.object(net_checkin_runtime, "STATE_PATH", root / "runtime.json")
        self.settings_patch = patch.object(net_checkin_state, "STATE_PATH", root / "settings.json")
        self.runtime_patch.start()
        self.settings_patch.start()
        self.admin_patch = patch.object(net_checkin_runtime, "ADMIN_CALLSIGN", "BG1ADM")
        self.admin_patch.start()

    def tearDown(self):
        self.settings_patch.stop()
        self.runtime_patch.stop()
        self.admin_patch.stop()
        self.directory.cleanup()

    def test_admin_start_and_end_commands(self):
        started = net_checkin_runtime.handle_admin_command(
            "BG1ADM", "我是机婶婶，我宣布台网点名活动开始", True
        )
        self.assertEqual(started["action"], "started")
        self.assertEqual(net_checkin_runtime.load()["status"], "active")
        ended = net_checkin_runtime.handle_admin_command(
            "BG1ADM", "我是机婶婶，我宣布台网点名活动结束", True
        )
        self.assertEqual(ended["action"], "ended")
        self.assertIsNone(net_checkin_runtime.parse_admin_command("BG1ABC", "我是机婶婶我宣布台网点名活动开始"))

    def test_disabled_gate_does_not_start(self):
        result = net_checkin_runtime.handle_admin_command("BG1ADM", "我是机婶婶我宣布台网点名活动开始", False)
        self.assertEqual(result["action"], "disabled")
        self.assertEqual(net_checkin_runtime.load()["status"], "idle")

    def test_one_current_callsign_without_waiting_queue(self):
        net_checkin_runtime.start(now=100)
        first = net_checkin_runtime.handle_participant("BH1AAA", "BH1AAA报到", now=101)
        self.assertEqual(first["action"], "ask_details")
        for index in range(4):
            result = net_checkin_runtime.handle_participant(f"BH1A{index}B", "报到", now=102 + index)
            self.assertEqual(result["action"], "busy_current")
        self.assertEqual(net_checkin_runtime.load()["waiting"], [])
        detail = net_checkin_runtime.handle_participant(
            "BH1AAA", "QTH北京，设备IC7300，功率20瓦", now=111,
            details={"qth": "北京", "device": "IC-7300", "power": "20瓦"},
        )
        self.assertEqual(detail["action"], "confirm_details")
        confirmed = net_checkin_runtime.handle_participant("BH1AAA", "正确", now=112)
        self.assertEqual(confirmed["action"], "confirmed")
        self.assertIsNone(confirmed["state"]["current"])
        self.assertEqual(confirmed["state"]["waiting"], [])

    def test_callsign_uses_icao_spelling_alphabet(self):
        self.assertEqual(
            net_checkin_runtime.spoken_callsign("BH1JSS"),
            "Bravo Hotel One Juliett Sierra Sierra",
        )

    def test_power_level_is_spoken_as_chinese_label(self):
        net_checkin_runtime.start(now=100)
        net_checkin_runtime.handle_participant("BH1AAA", "报到", now=101)
        result = net_checkin_runtime.handle_participant(
            "BH1AAA", "QTH北京，设备手台，低功率", now=102,
            details={"qth": "北京", "device": "手台", "power": "L"},
        )
        self.assertIn("功率 低功率", result["reply"])
        self.assertNotIn("功率 L", result["reply"])

    def test_completed_callsign_gets_guard_listening_reply(self):
        net_checkin_runtime.start(now=100)
        net_checkin_runtime.handle_participant("BH1AAA", "报到", now=101)
        net_checkin_runtime.handle_participant(
            "BH1AAA", "QTH北京，设备手台，低功率", now=102,
            details={"qth": "北京", "device": "手台", "power": "L"},
        )
        net_checkin_runtime.handle_participant("BH1AAA", "正确", now=103)
        duplicate = net_checkin_runtime.handle_participant("BH1AAA", "再次报到", now=104)
        self.assertEqual(duplicate["action"], "duplicate")
        self.assertIn("已经参与本次台网点名并记录", duplicate["reply"])
        self.assertIn("请您保持守听，有请下一位友台", duplicate["reply"])

    def test_first_reply_immediately_spells_mqtt_callsign(self):
        net_checkin_runtime.start(now=100)
        result = net_checkin_runtime.handle_participant(
            "BH1OFJ", "ASR可能误识别成BH1JSS", now=101, mqtt_callsign="BH1OFJ",
        )
        self.assertEqual(result["action"], "ask_details")
        self.assertEqual(result["state"]["current"]["callsign"], "BH1OFJ")
        self.assertIn("Bravo Hotel One Oscar Foxtrot Juliett", result["reply"])

    def test_confirmation_rejects_missing_required_fields(self):
        net_checkin_runtime.start(now=100)
        net_checkin_runtime.handle_participant("BH1AAA", "报到", now=101)
        net_checkin_runtime.handle_participant(
            "BH1AAA", "QTH北京", now=102,
            details={"qth": "北京", "device": "", "power": ""},
        )
        result = net_checkin_runtime.handle_participant("BH1AAA", "正确", now=103)
        self.assertEqual(result["action"], "missing_details")
        self.assertIn("设备、发射功率", result["reply"])

    def test_other_callsign_stays_busy_without_queue(self):
        net_checkin_runtime.start(now=100)
        net_checkin_runtime.handle_participant("BH1AAA", "报到", now=101)
        first = net_checkin_runtime.handle_participant("BH1BBB", "报到", now=102)
        repeated = net_checkin_runtime.handle_participant("BH1BBB", "再次报到", now=103)
        self.assertEqual(first["action"], "busy_current")
        self.assertEqual(repeated["action"], "busy_current")
        self.assertEqual(repeated["state"]["waiting"], [])

    def test_missing_power_supplement_preserves_qth_and_device(self):
        net_checkin_runtime.start(now=100)
        net_checkin_runtime.handle_participant("BH1AAA", "报到", now=101)
        first = net_checkin_runtime.handle_participant(
            "BH1AAA", "QTH北京设备手台", now=102,
            details={"qth": "北京", "device": "手台", "power": ""},
        )
        self.assertEqual(first["action"], "missing_details")
        self.assertIn("请只补充发射功率", first["reply"])
        second = net_checkin_runtime.handle_participant(
            "BH1AAA", "五瓦", now=103, details={"qth": "", "device": "", "power": "5W"},
        )
        current = second["state"]["current"]
        self.assertEqual((current["qth"], current["device"], current["power"]), ("北京", "手台", "5W"))
        self.assertEqual(second["action"], "confirm_details")

    def test_three_unanswered_calls_release_current_without_queue(self):
        net_checkin_runtime.start(now=100)
        net_checkin_runtime.handle_participant("BH1AAA", "报到", now=101)
        net_checkin_runtime.handle_participant("BH1BBB", "报到", now=102)
        net_checkin_runtime.mark_prompt_sent("BH1AAA", now=110)
        plan2 = net_checkin_runtime.retry_plan(now=118)
        self.assertEqual(plan2["next_attempts"], 2)
        net_checkin_runtime.commit_retry_plan(plan2, now=118)
        plan3 = net_checkin_runtime.retry_plan(now=126)
        self.assertEqual(plan3["next_attempts"], 3)
        net_checkin_runtime.commit_retry_plan(plan3, now=126)
        skipped = net_checkin_runtime.retry_plan(now=134)
        self.assertEqual(skipped["action"], "skip_no_response")
        self.assertEqual(skipped["next_callsign"], "")
        state = net_checkin_runtime.commit_retry_plan(skipped, now=134)
        self.assertIsNone(state["current"])
        self.assertEqual(state["skipped"][0]["callsign"], "BH1AAA")

    def test_current_participant_ptt_pauses_retry_until_processing_finishes(self):
        net_checkin_runtime.start(now=100)
        net_checkin_runtime.handle_participant("BH1AAA", "报到", now=101)
        net_checkin_runtime.mark_prompt_sent("BH1AAA", now=102)
        self.assertEqual(net_checkin_runtime.load()["current"]["response_deadline"], 110)
        paused = net_checkin_runtime.mark_response_started("BH1AAA")
        self.assertEqual(paused["current"]["response_deadline"], 0)
        self.assertIsNone(net_checkin_runtime.retry_plan(now=999))
        resumed = net_checkin_runtime.resume_response_timeout("BH1AAA", now=200)
        self.assertEqual(resumed["current"]["response_deadline"], 208)

    def test_mqtt_candidate_registration_does_not_queue(self):
        net_checkin_runtime.start(now=100)
        net_checkin_runtime.handle_participant("BH1AAA", "报到", now=101)
        for index in range(4):
            result = net_checkin_runtime.register_mqtt_candidate(f"BH1B{index}B", now=102 + index)
            self.assertEqual(result["action"], "busy_current")
        self.assertEqual(result["state"]["waiting"], [])

    def test_structured_fields_can_be_corrected_before_confirmation(self):
        net_checkin_runtime.start(now=100)
        net_checkin_runtime.handle_participant("BH1AAA", "报到", now=101)
        first = net_checkin_runtime.handle_participant(
            "BH1AAA", "北京，手台，五瓦", now=102,
            details={"qth": "北京", "device": "手台", "power": "5瓦"},
        )
        self.assertIn("请问主控抄收是否正确", first["reply"])
        corrected = net_checkin_runtime.handle_participant(
            "BH1AAA", "不对，QTH天津，设备FT5D，功率三瓦", now=103,
            details={"qth": "天津", "device": "FT5D", "power": "3瓦"},
        )
        self.assertEqual(corrected["action"], "retry_details")
        revised = net_checkin_runtime.handle_participant(
            "BH1AAA", "QTH天津，设备FT5D，功率三瓦", now=104,
            details={"qth": "天津", "device": "FT5D", "power": "3瓦"},
        )
        self.assertEqual(revised["state"]["current"]["qth"], "天津")
        confirmed = net_checkin_runtime.handle_participant("BH1AAA", "正确", now=105)
        self.assertIn("今天第1位友台", confirmed["reply"])
        self.assertNotIn("今晚", confirmed["reply"])
        self.assertIn("有请下一位友台", confirmed["reply"])

    def test_schedule_detection(self):
        net_checkin_state.save({
            "start_time": "20:00",
            "end_time": "21:30",
            "opening_text": "开始",
            "question_text": "{callsign}请报告",
            "closing_text": "结束",
        })
        self.assertEqual(net_checkin_runtime.scheduled_action(datetime(2026, 8, 16, 20, 0)), "start")
        net_checkin_runtime.start(now=100)
        self.assertEqual(net_checkin_runtime.scheduled_action(datetime(2026, 8, 16, 21, 30)), "end")

    def test_schedule_starts_after_busy_configured_minute(self):
        net_checkin_state.save({
            "start_time": "20:00", "end_time": "20:15", "opening_text": "开始",
            "question_text": "{callsign}请报告", "closing_text": "结束",
        })
        self.assertEqual(net_checkin_runtime.scheduled_action(datetime(2026, 8, 16, 20, 2)), "start")

    def test_schedule_honors_once_and_weekly_modes(self):
        base = {
            "start_time": "20:00", "end_time": "20:15", "opening_text": "开始",
            "question_text": "{callsign}请报告", "closing_text": "结束", "schedule_date": "2026-08-17",
            "weekly_day": 0,
        }
        net_checkin_state.save({**base, "repeat_enabled": False, "repeat_type": "daily"})
        self.assertIsNone(net_checkin_runtime.scheduled_action(datetime(2026, 8, 16, 20, 0)))
        self.assertEqual(net_checkin_runtime.scheduled_action(datetime(2026, 8, 17, 20, 0)), "start")
        net_checkin_state.save({**base, "repeat_enabled": True, "repeat_type": "weekly"})
        self.assertIsNone(net_checkin_runtime.scheduled_action(datetime(2026, 8, 18, 20, 0)))
        self.assertEqual(net_checkin_runtime.scheduled_action(datetime(2026, 8, 24, 20, 0)), "start")

    def test_end_waits_for_current_participant_then_closes(self):
        net_checkin_state.save({
            "start_time": "20:00", "end_time": "20:15", "opening_text": "开始",
            "question_text": "{callsign}请报告", "closing_text": "结束",
        })
        net_checkin_runtime.start(now=datetime(2026, 8, 16, 20, 0).timestamp())
        net_checkin_runtime.handle_participant("BH1AAA", "报到", now=101)
        self.assertEqual(net_checkin_runtime.scheduled_action(datetime(2026, 8, 16, 20, 15)), "mark_closing")
        net_checkin_runtime.mark_closing_pending()
        ignored = net_checkin_runtime.handle_participant("BH1BBB", "报到", now=102)
        self.assertEqual(ignored["action"], "ignored_closing")
        net_checkin_runtime.handle_participant(
            "BH1AAA", "北京设备五瓦", now=103,
            details={"qth": "北京", "device": "手台", "power": "5W"},
        )
        confirmed = net_checkin_runtime.handle_participant("BH1AAA", "正确", now=104)
        self.assertIn("本条记录完成后结束活动", confirmed["reply"])
        self.assertNotIn("有请下一位", confirmed["reply"])
        self.assertEqual(net_checkin_runtime.scheduled_action(datetime(2026, 8, 16, 20, 15)), "end")

    def test_end_clears_unfinished_runtime_state(self):
        net_checkin_runtime.start(now=100)
        net_checkin_runtime.handle_participant("BH1AAA", "报到", now=101)
        state = net_checkin_runtime.end(now=102)["state"]
        self.assertEqual(state["status"], "finished")
        self.assertIsNone(state["current"])
        self.assertFalse(state["closing_pending"])

    def test_hard_end_skips_stuck_current_after_grace_period(self):
        net_checkin_state.save({
            "start_time": "17:59", "end_time": "19:00", "opening_text": "开始",
            "question_text": "{callsign}请报告", "closing_text": "结束",
        })
        net_checkin_runtime.start(now=datetime(2026, 8, 18, 17, 59).timestamp())
        net_checkin_runtime.handle_participant("BG2IKH", "报到", now=101)
        closing_time = datetime(2026, 8, 18, 19, 0)
        self.assertEqual(net_checkin_runtime.scheduled_action(closing_time), "mark_closing")
        net_checkin_runtime.mark_closing_pending(now=closing_time.timestamp())
        self.assertEqual(
            net_checkin_runtime.scheduled_action(datetime(2026, 8, 18, 19, 1, 59)),
            "mark_closing",
        )
        self.assertEqual(
            net_checkin_runtime.scheduled_action(datetime(2026, 8, 18, 19, 2)),
            "force_end",
        )
        result = net_checkin_runtime.force_end(now=datetime(2026, 8, 18, 19, 2).timestamp())
        state = result["state"]
        self.assertEqual(result["action"], "force_ended")
        self.assertEqual(state["status"], "finished")
        self.assertIsNone(state["current"])
        self.assertFalse(state["closing_pending"])
        self.assertEqual(state["skipped"][0]["callsign"], "BG2IKH")
        self.assertEqual(state["skipped"][0]["skip_reason"], "hard_end_deadline")


if __name__ == "__main__":
    unittest.main()
