"""Working-day date helpers for inspection re-inspection timers."""

from datetime import UTC, date, datetime, timedelta


def _is_working_day(d: date, holidays: set[date] | None = None) -> bool:
    if d.weekday() >= 5:
        return False
    if holidays and d in holidays:
        return False
    return True


def add_working_days(start: datetime, days: int, holidays: set[date] | None = None) -> datetime:
    holidays = holidays or set()
    current = start
    added = 0
    while added < days:
        current = current + timedelta(days=1)
        if _is_working_day(current.date(), holidays):
            added += 1
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current
