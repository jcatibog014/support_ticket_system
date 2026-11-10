"""SLA: the time limits (Service Level Agreement) every ticket runs against.

Each priority has two limits, in WORKING minutes (hours.py):
  - first reply: the team's first public reply;
  - solved: the ticket resolved.
They are stored on the ticket as due times (response_due_at, resolve_due_at),
counted from when the ticket was sent. Changing the priority works them out
again. Waiting on the customer stops the "solved" clock: the time spent waiting
(paused_minutes) is added on when the ticket comes back.

A clock is:
  - "met" or "missed" once it is over (replied / resolved, in time or late);
  - "late" when its due time has passed;
  - "soon" when less than a quarter of its limit (at least 30 minutes) is left;
  - "ok" otherwise, or "paused" while waiting on the customer.

tick.py escalates late tickets to the Team Leads.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select

from . import hours
from .models import ACTIVE, HIGH, IN_PROGRESS, LOW, NEW, NORMAL, OPEN, PRIORITIES, URGENT, WAITING, SlaRule, Ticket

# (first reply, solved) in working minutes. A working day is 9 hours (8 AM to 5 PM).
DEFAULT_RULES = {
    URGENT: (60, 4 * 60),
    HIGH: (4 * 60, 2 * 9 * 60),
    NORMAL: (9 * 60, 5 * 9 * 60),
    LOW: (2 * 9 * 60, 10 * 9 * 60),
}
SOON_SHARE = 0.25
SOON_AT_LEAST = 30


@dataclass(frozen=True)
class Clock:
    """One SLA clock as people see it."""
    kind: str  # "response" or "resolve"
    state: str  # met, missed, late, soon, ok, paused
    due: datetime | None
    minutes: int  # working minutes left (ok, soon) or over (late, missed)

    @property
    def label(self):
        what = "First reply" if self.kind == "response" else "Solved"
        return {
            "met": f"{what} in time",
            "missed": f"{what} {short(self.minutes)} late",
            "late": f"{what} {short(self.minutes)} late",
            "soon": f"{what} due in {short(self.minutes)}",
            "ok": f"{what} due in {short(self.minutes)}",
            "paused": f"{what}: clock paused",
        }[self.state]


def short(minutes):
    """Working time: 45 min, 3 h, 3 h 20 min (no days: a working day isn't 24 hours)."""
    minutes = max(0, int(minutes))
    if minutes < 60:
        return f"{minutes} min"
    hours_, rest = divmod(minutes, 60)
    return f"{hours_} h {rest} min" if rest else f"{hours_} h"


def make_default_rules(s, desk):
    for priority, (response, resolve) in DEFAULT_RULES.items():
        s.add(SlaRule(desk_id=desk.id, priority=priority, response_minutes=response, resolve_minutes=resolve))


def rules_for(s, desk):
    """{priority: (first reply, solved)}, with the defaults for anything missing."""
    found = {rule.priority: (rule.response_minutes, rule.resolve_minutes)
             for rule in s.scalars(select(SlaRule).where(SlaRule.desk_id == desk.id))}
    return {priority: found.get(priority, DEFAULT_RULES[priority]) for priority in PRIORITIES}


def due_times(desk, created_at, priority, paused_minutes, rules, holidays):
    """{"response_due_at": ..., "resolve_due_at": ...} for a ticket with this priority."""
    response, resolve = rules[priority]
    return {"response_due_at": hours.add_minutes(desk, created_at, response, holidays),
            "resolve_due_at": hours.add_minutes(desk, created_at, resolve + paused_minutes, holidays)}


def targets(s, desk, ticket, priority=None, paused_minutes=None):
    """due_times() for a ticket, reading the rules and holidays."""
    return due_times(desk, ticket.created_at, priority or ticket.priority,
                     ticket.paused_minutes if paused_minutes is None else paused_minutes,
                     rules_for(s, desk), hours.holiday_days(s, desk))


def _clock(kind, desk, due, done_at, target, now, holidays, paused=False):
    if due is None:
        return None
    if done_at is not None:
        late = hours.minutes_between(desk, due, done_at, holidays)
        return Clock(kind, "missed" if late > 0 else "met", due, late)
    if paused:
        return Clock(kind, "paused", due, 0)
    if now >= due:
        return Clock(kind, "late", due, hours.minutes_between(desk, due, now, holidays))
    left = hours.minutes_between(desk, now, due, holidays)
    soon = left < max(SOON_AT_LEAST, target * SOON_SHARE)
    return Clock(kind, "soon" if soon else "ok", due, left)


def clocks(ticket, desk, now, rules, holidays):
    """(first reply clock, solved clock) for a ticket; either can be None."""
    response, resolve = rules[ticket.priority]
    reply = _clock("response", desk, ticket.response_due_at, ticket.first_response_at, response, now, holidays)
    solved = None
    if ticket.status in ACTIVE or ticket.resolved_at is not None:
        solved = _clock("resolve", desk, ticket.resolve_due_at, ticket.resolved_at, resolve, now, holidays,
                        paused=ticket.status == WAITING)
    return reply, solved


def headline(ticket, desk, now, rules, holidays):
    """The one clock a list shows: the first reply until it happens, then the fix. None when done."""
    if ticket.status not in ACTIVE:
        return None
    reply, solved = clocks(ticket, desk, now, rules, holidays)
    if reply is not None and reply.state in ("late", "soon", "ok"):
        return reply
    return solved


class Context:
    """Rules and holidays read once, for drawing many tickets' clocks on one page."""

    def __init__(self, s, desk, now):
        self.desk, self.now = desk, now
        self.rules, self.holidays = rules_for(s, desk), hours.holiday_days(s, desk)

    def headline(self, ticket):
        return headline(ticket, self.desk, self.now, self.rules, self.holidays)

    def clocks(self, ticket):
        return clocks(ticket, self.desk, self.now, self.rules, self.holidays)


def overdue_filter(now):
    """The WHERE for active tickets past a time limit. The fix clock doesn't count while Waiting."""
    return and_(Ticket.status.in_(ACTIVE), or_(
        and_(Ticket.first_response_at.is_(None), Ticket.response_due_at < now),
        and_(Ticket.status.in_((NEW, OPEN, IN_PROGRESS)), Ticket.resolve_due_at < now),
    ))
