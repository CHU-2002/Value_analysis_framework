"""Exchange-local dates and conservative regular-session close cutoffs."""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


CURRENCY_MARKETS = {"CNY": "A", "HKD": "HK", "USD": "US"}
MARKET_ZONES = {"A": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}
# HK closing auction ends by 16:10. Early closes wait for the normal cutoff.
SESSION_CLOSES = {"A": time(15), "HK": time(16, 10), "US": time(16)}


def market_time(market, now=None):
    now = now if now is not None else datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("market time requires a timezone-aware timestamp")
    return now.astimezone(ZoneInfo(MARKET_ZONES[market]))


def daily_close_complete(day, market, now):
    local = market_time(market, now)
    return day.weekday() < 5 and (day < local.date() or
                                 (day == local.date() and local.time() >= SESSION_CLOSES[market]))
