"""Find the last Paris 13:00 slot at creation of a scheduled GitHub run.

GitHub keeps created_at across attempts. A rerun after midnight therefore
refers to the original slot even on a runner with a different system timezone.
"""

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def slot_date(created_at: str) -> str:
    instant = datetime.fromisoformat(created_at)
    if instant.tzinfo is None:
        raise ValueError("Expected a timezone-aware GitHub run creation timestamp")
    created = instant.astimezone(ZoneInfo("Europe/Paris"))
    day = created.date()
    if created.hour < 13:
        day -= timedelta(days=1)
    return day.isoformat()


if __name__ == "__main__":
    print(slot_date(sys.argv[1]))
