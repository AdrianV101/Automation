from datetime import datetime


def parse_date(recorded_at: str) -> datetime:
    try:
        return datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now()
