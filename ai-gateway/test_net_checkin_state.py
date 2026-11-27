import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import net_checkin_state


class NetCheckinStateTests(unittest.TestCase):
    def test_defaults_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(net_checkin_state, "STATE_PATH", Path(directory) / "state.json"):
            self.assertIn("个人技术研究", net_checkin_state.load()["opening_text"])
            saved = net_checkin_state.save({
                "start_time": "20:00",
                "end_time": "21:30",
                "opening_text": "开始点名",
                "question_text": "请 {callsign} 报QTH、设备和功率",
                "closing_text": "点名结束，73",
            })
            self.assertEqual(saved, net_checkin_state.load())
            self.assertEqual(Path(directory, "state.json").stat().st_mode & 0o777, 0o600)

    def test_rejects_missing_or_empty_fields(self):
        with self.assertRaises(ValueError):
            net_checkin_state.save({"opening_text": "开始"})
        with self.assertRaises(ValueError):
            net_checkin_state.save({"start_time": "", "end_time": "", "opening_text": "开始", "question_text": "", "closing_text": "结束"})
        with self.assertRaises(ValueError):
            net_checkin_state.save({"start_time": "25:00", "end_time": "", "opening_text": "开始", "question_text": "询问", "closing_text": "结束"})

    def test_rejects_start_within_two_minutes_and_accepts_recurrence(self):
        base = {
            "repeat_enabled": False, "repeat_type": "daily", "schedule_date": "2026-08-18",
            "weekly_day": 0, "start_time": "21:01", "end_time": "21:30",
            "opening_text": "开始", "question_text": "询问", "closing_text": "结束",
        }
        now = datetime(2026, 8, 18, 21, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with tempfile.TemporaryDirectory() as directory, patch.object(net_checkin_state, "STATE_PATH", Path(directory) / "state.json"):
            with self.assertRaisesRegex(ValueError, "至少晚于当前时间2分钟"):
                net_checkin_state.save(base, now=now)
            saved = net_checkin_state.save({**base, "start_time": "21:02"}, now=now)
            self.assertFalse(saved["repeat_enabled"])
            weekly = net_checkin_state.save({
                **base, "repeat_enabled": True, "repeat_type": "weekly", "weekly_day": 2,
                "schedule_date": "", "start_time": "21:01",
            }, now=now)
            self.assertEqual(weekly["repeat_type"], "weekly")


if __name__ == "__main__":
    unittest.main()
