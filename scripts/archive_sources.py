from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime


def conservative_available_at(release_date, last_modified=None, retrieved_at=None):
    """Return a conservative UTC date bound, not proof of historical availability."""
    try:
        release = date.fromisoformat(release_date)
        modified = parsedate_to_datetime(last_modified)
        retrieved_value = (
            retrieved_at[:-1] + "+00:00"
            if isinstance(retrieved_at, str) and retrieved_at.endswith("Z")
            else retrieved_at
        )
        retrieved = datetime.fromisoformat(retrieved_value)

        if modified.tzinfo is None or modified.utcoffset() is None:
            raise ValueError("Last-Modified must include a timezone")
        if retrieved.tzinfo is None or retrieved.utcoffset() is None:
            raise ValueError("retrieved_at must include a timezone")

        modified = modified.astimezone(timezone.utc)
        retrieved = retrieved.astimezone(timezone.utc)
        if modified > retrieved:
            raise ValueError("Last-Modified cannot be later than retrieved_at")
        if release > retrieved.date() + timedelta(days=1):
            raise ValueError("release_date is too far in the future")

        available_date = max(release, modified.date()) + timedelta(days=2)
        return datetime.combine(available_date, time.min, tzinfo=timezone.utc).isoformat()
    except (TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("invalid date or timestamp metadata") from exc
