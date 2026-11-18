"""The friend group's clock: Bangkok time, and how long ago something happened, in words."""
from datetime import datetime, timedelta, timezone
from typing import Optional

# The friend group lives in Bangkok, so days and "now" are told in its time
BANGKOK = timezone(timedelta(hours=7))


def as_time(value) -> Optional[datetime]:
    """A datetime or an ISO string as an aware datetime. Naive times are local, as the bot stores them."""
    if isinstance(value, datetime):
        sent = value
    else:
        try:
            sent = datetime.fromisoformat(value or "")
        except (TypeError, ValueError):
            return None
    return sent if sent.tzinfo else sent.astimezone()


def days_ago(sent: datetime, now: Optional[datetime] = None) -> str:
    """'today', 'yesterday' or 'N days ago', counted in Bangkok calendar days."""
    now = now or datetime.now(timezone.utc)
    days = (now.astimezone(BANGKOK).date() - sent.astimezone(BANGKOK).date()).days
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days} days ago"


def day_header(sent: datetime, now: Optional[datetime] = None) -> str:
    """A line that starts one day of messages: '--- Saturday 31 Oct (18 days ago) ---'."""
    local = sent.astimezone(BANGKOK)
    return f"--- {local:%A} {local.day} {local:%b} ({days_ago(sent, now)}) ---"
