import unittest
from datetime import datetime, timedelta, timezone

from announcement_schedule import night_notice_slot


class NightNoticeTests(unittest.TestCase):
    def test_expected_notice_minutes(self):
        tz = timezone(timedelta(hours=8))
        expected = {50, 52, 54, 56, 58, 59}
        actual = {
            minute for minute in range(60)
            if night_notice_slot(datetime(2026, 8, 3, 23, minute, tzinfo=tz))
        }
        self.assertEqual(actual, expected)

    def test_notice_does_not_run_outside_2350_to_2359(self):
        tz = timezone(timedelta(hours=8))
        self.assertIsNone(night_notice_slot(datetime(2026, 8, 3, 22, 50, tzinfo=tz)))
        self.assertIsNone(night_notice_slot(datetime(2026, 8, 4, 0, 0, tzinfo=tz)))


if __name__ == "__main__":
    unittest.main()
