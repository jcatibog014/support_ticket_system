"""Insights (Team Leads and Admins): how the desk is doing, as numbers and the shapes of charts.

The charts are SVG drawn by the server (insights.html): no chart library,
nothing to download. This module works out the numbers.

Colours were checked with the dataviz validator, light and dark: the two
lines of "Sent and Solved" (blue and orange) are easy to tell apart, also for
colour-blind readers. Every other chart has one series in one colour, with the
names on the axis, so colour never carries meaning alone. Every chart can open
as a table.

A help desk has hundreds of tickets a month, not millions, so the numbers are
worked out in Python from one query per chart. Times are WORKING time
(hours.py), the same clock as the SLA limits.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from statistics import median

from sqlalchemy import func, select

from . import hours
from .dates import from_local, to_local
from .models import ACTIVE, PRIORITIES, PRIORITY_NAMES, STAFF_ROLES, STATUS_NAMES, Ticket, User

PERIODS = {7: "Last 7 days", 30: "Last 30 days", 90: "Last 90 days"}


@dataclass
class Rate:
    met: int
    missed: int

    @property
    def total(self):
        return self.met + self.missed

    @property
    def percent(self):
        return round(100 * self.met / self.total) if self.total else None


def start_of(days, now):
    """Midnight (Philippine time) `days - 1` days ago, as naive UTC: "the last 7 days" includes today."""
    first = to_local(now).date() - timedelta(days=days - 1)
    return from_local(datetime.combine(first, time())), first


def tickets_since(s, desk, since):
    """Tickets sent, answered, resolved or rated since `since` (one query for most charts)."""
    return s.scalars(select(Ticket).where(Ticket.desk_id == desk.id, (Ticket.created_at >= since)
                                          | (Ticket.first_response_at >= since) | (Ticket.resolved_at >= since)
                                          | (Ticket.rated_at >= since))).all()


def open_by_status(s, desk):
    """[(status, name, count)] of the tickets someone still has to work on."""
    found = dict(s.execute(select(Ticket.status, func.count()).where(Ticket.desk_id == desk.id,
                                                                     Ticket.status.in_(ACTIVE))
                           .group_by(Ticket.status)).all())
    return [(status, STATUS_NAMES[status], found.get(status, 0)) for status in ACTIVE]


def sent_and_solved(rows, first_day, days):
    """[(label, first day, sent, solved)] per day, or per week for 90 days, oldest first."""
    step = 7 if days > 31 else 1
    buckets = []
    day = first_day
    last = first_day + timedelta(days=days - 1)
    while day <= last:
        buckets.append([day, 0, 0])
        day += timedelta(days=step)
    def bucket(moment):
        index = (to_local(moment).date() - first_day).days // step
        return buckets[index] if 0 <= index < len(buckets) else None
    for ticket in rows:
        if (found := bucket(ticket.created_at)) is not None:
            found[1] += 1
        if ticket.resolved_at is not None and (found := bucket(ticket.resolved_at)) is not None:
            found[2] += 1
    return [(f"{start:%b} {start.day}", start, sent, solved) for start, sent, solved in buckets], step


def sla_rates(rows, since, now):
    """(first reply Rate, solved Rate) for tickets sent in the period whose clock is decided."""
    reply, solved = Rate(0, 0), Rate(0, 0)
    for ticket in rows:
        if ticket.created_at < since:
            continue
        if ticket.response_due_at is not None:
            if ticket.first_response_at is not None:
                if ticket.first_response_at <= ticket.response_due_at:
                    reply.met += 1
                else:
                    reply.missed += 1
            elif ticket.response_due_at < now:
                reply.missed += 1
        if ticket.resolve_due_at is not None:
            if ticket.resolved_at is not None:
                if ticket.resolved_at <= ticket.resolve_due_at:
                    solved.met += 1
                else:
                    solved.missed += 1
            elif ticket.status in ACTIVE and ticket.paused_since is None and ticket.resolve_due_at < now:
                solved.missed += 1
    return reply, solved


def median_times(rows, desk, holidays, since, done_field):
    """[(priority, name, median working minutes or None, how many)] for tickets whose
    first reply (done_field="first_response_at") or fix ("resolved_at") came in the period."""
    times = {priority: [] for priority in PRIORITIES}
    for ticket in rows:
        done = getattr(ticket, done_field)
        if done is None or done < since:
            continue
        minutes = hours.minutes_between(desk, ticket.created_at, done, holidays)
        if done_field == "resolved_at":
            minutes = max(0, minutes - ticket.paused_minutes)  # time waiting on the customer doesn't count
        times[ticket.priority].append(minutes)
    return [(priority, PRIORITY_NAMES[priority], round(median(found)) if found else None, len(found))
            for priority, found in reversed(list(times.items()))]


def team_load(s, desk, rows, since):
    """[(person, open now, solved in the period, average rating or None)], busiest first."""
    staff = s.scalars(select(User).where(User.desk_id == desk.id, User.role.in_(STAFF_ROLES),
                                         User.is_active.is_(True)).order_by(User.name)).all()
    open_now = dict(s.execute(select(Ticket.assignee_id, func.count()).where(
        Ticket.desk_id == desk.id, Ticket.status.in_(ACTIVE), Ticket.assignee_id.is_not(None))
        .group_by(Ticket.assignee_id)).all())
    solved, ratings = {}, {}
    for ticket in rows:
        if ticket.assignee_id is None:
            continue
        if ticket.resolved_at is not None and ticket.resolved_at >= since:
            solved[ticket.assignee_id] = solved.get(ticket.assignee_id, 0) + 1
        if ticket.rating and ticket.rated_at and ticket.rated_at >= since:
            ratings.setdefault(ticket.assignee_id, []).append(ticket.rating)
    result = [(person, open_now.get(person.id, 0), solved.get(person.id, 0),
               round(sum(ratings[person.id]) / len(ratings[person.id]), 1) if person.id in ratings else None)
              for person in staff]
    result.sort(key=lambda row: (-row[1], -row[2], row[0].name.lower()))
    return result


def ratings(rows, since):
    """([(stars, count)] from 5 down to 1, average or None, how many)."""
    counts = {stars: 0 for stars in range(5, 0, -1)}
    for ticket in rows:
        if ticket.rating and ticket.rated_at and ticket.rated_at >= since:
            counts[ticket.rating] += 1
    total = sum(counts.values())
    average = round(sum(stars * count for stars, count in counts.items()) / total, 1) if total else None
    return list(counts.items()), average, total


def bar(x, y, width, height, end="right", radius=4):
    """An SVG path for a bar whose far end is rounded and whose base is square."""
    if width <= 0 or height <= 0:
        return ""
    r = min(radius, width, height / 2) if end == "right" else min(radius, width / 2, height)
    if end == "right":
        return (f"M{x:.1f},{y:.1f} h{width - r:.1f} a{r},{r} 0 0 1 {r},{r} v{height - 2 * r:.1f} "
                f"a{r},{r} 0 0 1 -{r},{r} h-{width - r:.1f} z")
    return (f"M{x:.1f},{y + height:.1f} v-{height - r:.1f} a{r},{r} 0 0 1 {r},-{r} h{width - 2 * r:.1f} "
            f"a{r},{r} 0 0 1 {r},{r} v{height - r:.1f} z")


def line_points(values, width, height, top, most):
    """'x,y x,y ...' for an SVG polyline, and the points themselves."""
    count = len(values)
    step = width / (count - 1) if count > 1 else 0
    points = [(round(i * step, 1), round(top + height - height * value / most, 1)) for i, value in enumerate(values)]
    return " ".join(f"{x},{y}" for x, y in points), points


def axis(value):
    """(top, step) for an axis that fits the data: at most 5 steps, each 1, 2, 5, 10, 20, 25, 50...
    A busiest day of 3 gives 0-3; 6 gives 0-6 by 2; 13 gives 0-15 by 5; 47 gives 0-50 by 10."""
    value = max(int(value), 1)
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        steps = -(-value // step)
        if steps <= 5:
            return steps * step, step
    return value, value


def nice_max(value):
    """The top of axis()."""
    return axis(value)[0]


def time_rows(found):
    """median_times() as chart rows: (label, value, text beside the bar, hover text)."""
    from .formatting import plural
    from .sla import short
    return [(name, minutes, short(minutes) if minutes is not None else "No tickets",
             f"{name}: {short(minutes)} for the middle one of {plural(count, 'ticket')}" if minutes is not None
             else f"{name}: no tickets")
            for _, name, minutes, count in found]


def init_app(app):
    app.jinja_env.globals.update(bar_path=bar, line_points=line_points)
