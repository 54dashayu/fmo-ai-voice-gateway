from __future__ import annotations

from datetime import datetime


NIGHT_NOTICE_MINUTES = (50, 52, 54, 56, 58, 59)


def night_notice_slot(local_now: datetime) -> str | None:
    if local_now.hour != 23 or local_now.minute not in NIGHT_NOTICE_MINUTES:
        return None
    return local_now.strftime("%Y-%m-%dT%H:%M%z")
