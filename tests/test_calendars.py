"""Market calendar helpers (timezone-aware)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from trading_algo import calendars
from trading_algo.regions import get_region

SYD = ZoneInfo("Australia/Sydney")
ASX = get_region("ASX")


def test_open_during_session():
    # Wed 2024-01-10, 12:00 Sydney -> open (10:00-16:00)
    dt = datetime(2024, 1, 10, 12, 0, tzinfo=SYD)
    assert calendars.is_market_open(ASX, dt) is True


def test_closed_on_weekend():
    sat = datetime(2024, 1, 13, 12, 0, tzinfo=SYD)
    assert calendars.is_market_open(ASX, sat) is False


def test_after_close():
    dt = datetime(2024, 1, 10, 17, 0, tzinfo=SYD)
    assert calendars.is_market_open(ASX, dt) is False
    assert calendars.is_after_close(ASX, dt) is True


def test_before_open_not_after_close():
    dt = datetime(2024, 1, 10, 9, 0, tzinfo=SYD)
    assert calendars.is_after_close(ASX, dt) is False


def test_next_close_is_future_weekday():
    dt = datetime(2024, 1, 10, 17, 0, tzinfo=SYD)   # after Wed close
    nxt = calendars.next_close(ASX, dt)
    assert nxt > dt
    assert nxt.weekday() < 5
    assert (nxt.hour, nxt.minute) == (ASX.market_close.hour, ASX.market_close.minute)
