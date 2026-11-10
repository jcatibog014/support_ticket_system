"""Working time: the clock the SLA limits run on.

A desk is open on its workdays between open_minute and close_minute
(Philippine time), except on its holidays. "4 working hours" from Friday
4 PM, with 8 AM to 5 PM on weekdays, is Monday 11 AM.

Everything here takes and gives naive UTC (like the database) and works in
Philippine time inside. Holidays are passed in as a set of dates, read once
(holiday_days), so a long list of tickets costs one query.
"""
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from .dates import from_local, to_local
from .models import Holiday

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MAX_DAYS = 800  # a limit far beyond any SLA, so a desk with no open days can't loop forever


class NeverOpen(ValueError):
    pass


def holiday_days(s, desk):
    return set(s.scalars(select(Holiday.day).where(Holiday.desk_id == desk.id)))


def is_open_day(desk, day, holidays):
    return str(day.weekday()) in desk.workdays and day not in holidays


def _local(moment):
    return to_local(moment).replace(tzinfo=None)


def _window(desk, day):
    start = datetime.combine(day, time()) + timedelta(minutes=desk.open_minute)
    return start, datetime.combine(day, time()) + timedelta(minutes=desk.close_minute)


def add_minutes(desk, start, minutes, holidays):
    """The moment `minutes` working minutes after `start`."""
    now = _local(start)
    remaining = max(0, int(minutes))
    for _ in range(MAX_DAYS):
        day = now.date()
        if is_open_day(desk, day, holidays):
            opens, closes = _window(desk, day)
            now = max(now, opens)
            if now < closes:
                free = (closes - now).total_seconds() / 60
                if remaining <= free:
                    return from_local(now + timedelta(minutes=remaining))
                remaining -= free
        now = datetime.combine(day + timedelta(days=1), time())
    raise NeverOpen("The desk has no working hours, so the time limits can't run.")


def minutes_between(desk, start, end, holidays):
    """Working minutes from start to end (0 if end isn't later)."""
    if end <= start:
        return 0
    first, last = _local(start), _local(end)
    total = 0.0
    day = first.date()
    while day <= last.date():
        if is_open_day(desk, day, holidays):
            opens, closes = _window(desk, day)
            begin, finish = max(opens, first), min(closes, last)
            if finish > begin:
                total += (finish - begin).total_seconds() / 60
        day += timedelta(days=1)
    return int(round(total))


def describe(desk):
    """"Monday to Friday, 8:00 AM to 5:00 PM" for the pages."""
    days = [int(d) for d in sorted(desk.workdays)]
    in_a_row = len(days) > 2 and days == list(range(days[0], days[-1] + 1))
    if in_a_row:
        span = f"{WEEKDAY_NAMES[days[0]]} to {WEEKDAY_NAMES[days[-1]]}"
    else:
        span = ", ".join(WEEKDAY_NAMES[d] for d in days) or "No days"
    return f"{span}, {clock(desk.open_minute)} to {clock(desk.close_minute)}"


def clock(minute):
    hour, rest = divmod(minute, 60)
    return f"{hour % 12 or 12}:{rest:02d} {'AM' if hour < 12 else 'PM'}"


# ---------- Philippine holidays ----------

def easter(year):
    """Easter Sunday (Gregorian), by the "anonymous" algorithm from 1876.
    Holy Week moves every year, so it has to be worked out, not typed in."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g_ = (b - f + 1) // 3
    h = (19 * a + b - d - g_ + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = (h + l_ - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def last_monday(year, month):
    """The last Monday of a month (National Heroes Day is the last Monday of August)."""
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=last.weekday())


def ph_holidays(year):
    """The regular holidays and the usual special non-working days, as (day, name).
    Eid'l Fitr, Eid'l Adha and Chinese New Year are announced each year, so
    the Admin adds those by hand."""
    sunday = easter(year)
    return sorted([
        (date(year, 1, 1), "New Year's Day"),
        (sunday - timedelta(days=3), "Maundy Thursday"),
        (sunday - timedelta(days=2), "Good Friday"),
        (sunday - timedelta(days=1), "Black Saturday"),
        (date(year, 4, 9), "Araw ng Kagitingan"),
        (date(year, 5, 1), "Labor Day"),
        (date(year, 6, 12), "Independence Day"),
        (date(year, 8, 21), "Ninoy Aquino Day"),
        (last_monday(year, 8), "National Heroes Day"),
        (date(year, 11, 1), "All Saints' Day"),
        (date(year, 11, 30), "Bonifacio Day"),
        (date(year, 12, 8), "Feast of the Immaculate Conception"),
        (date(year, 12, 24), "Christmas Eve"),
        (date(year, 12, 25), "Christmas Day"),
        (date(year, 12, 30), "Rizal Day"),
        (date(year, 12, 31), "Last Day of the Year"),
    ])


def add_ph_holidays(s, desk, year):
    """Add the year's holidays, skipping days the desk already has. Returns the ones added."""
    taken = holiday_days(s, desk)
    added = [Holiday(desk_id=desk.id, day=day, name=name) for day, name in ph_holidays(year) if day not in taken]
    s.add_all(added)
    return added
