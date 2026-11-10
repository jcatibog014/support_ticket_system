"""The status workflow: the ONLY place a ticket's status changes.

        ┌──────────────── Cancelled (the customer, before work starts; a Team Lead for spam)
        │
    New ──> Open ──> In Progress <──> Waiting on Customer
     │       │  <──────┘  │                │
     └───────┴────────────┴────────────────┴──> Resolved ──> Closed
                                                   │
                       Open <── reopened ──────────┘  (within 7 days)

Two kinds of rule, kept apart on purpose:
  - move_problem() is about the TICKET: can its status go from here to there,
    and is everything in place (an assignee, a resolution)? It doesn't care who asks.
  - permissions.can_move() is about the PERSON: may this role make this move
    on this ticket? It doesn't care whether the ticket is ready.
A move needs both. tests/test_workflow.py has a table for each.

move() checks the rules, changes the row with a conditional UPDATE
(... WHERE status = <what we read> AND version = <what we read>), and writes
the history. If someone else changed the ticket first, the UPDATE finds no
row and TicketChanged is raised, so nothing is overwritten.

Some moves happen by themselves (actor None = Ticket Tock): a customer's reply
brings a Waiting ticket back to In Progress, or reopens a Resolved one.
"""
from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value

from . import audit, hours, notify, sla
from .models import (CANCELLED, CLOSED, FINISHED, IN_PROGRESS, NEW, OPEN, RESOLVED, STATUS_NAMES, WAITING, Desk,
                     Ticket)

TRANSITIONS = {
    NEW: (OPEN, IN_PROGRESS, WAITING, RESOLVED, CANCELLED),
    OPEN: (IN_PROGRESS, WAITING, RESOLVED, CANCELLED),
    IN_PROGRESS: (OPEN, WAITING, RESOLVED),  # OPEN: put it down for now (it stays theirs)
    WAITING: (IN_PROGRESS, RESOLVED),
    RESOLVED: (OPEN, CLOSED),  # OPEN: reopened
    CLOSED: (),
    CANCELLED: (),
}
NEEDS_ASSIGNEE = (IN_PROGRESS, WAITING)
RESOLUTION_MAX = 2000


class MoveNotAllowed(ValueError):
    pass


class TicketChanged(Exception):
    """Someone else changed the ticket between our read and our write."""
    message = "Someone else changed this ticket a moment ago. Here it is again with their change. Please try once more."


def move_problem(ticket, to, resolution=""):
    """Why the ticket can't go to `to` right now, or None if it can."""
    if to == ticket.status:
        return f"It is already {STATUS_NAMES[to]}."
    if to not in TRANSITIONS.get(ticket.status, ()):
        return f"A ticket can't go from {STATUS_NAMES[ticket.status]} to {STATUS_NAMES.get(to, to)}."
    if to in NEEDS_ASSIGNEE and ticket.assignee_id is None:
        return "Give the ticket to someone first."
    if to == RESOLVED and not (resolution or "").strip():
        return "Write how it was solved, so the customer knows."
    if to == RESOLVED and len(resolution.strip()) > RESOLUTION_MAX:
        return f"Keep the resolution under {RESOLUTION_MAX} characters."
    return None


def move_label(ticket, to, is_customer=False):
    """The words on a move button."""
    if to == OPEN:
        return {NEW: "Mark as open", IN_PROGRESS: "Stop work for now", RESOLVED: "Reopen ticket"}[ticket.status]
    if to == IN_PROGRESS:
        return "Back to work" if ticket.status == WAITING else "Start work"
    if to == WAITING:
        return "Wait for customer"
    if to == RESOLVED:
        return "Resolve"
    if to == CLOSED:
        return "Yes, it's fixed. Close it" if is_customer else "Close ticket"
    if to == CANCELLED:
        return "Cancel my ticket" if is_customer else "Cancel ticket"
    return STATUS_NAMES[to]


def apply(s, ticket, now, **values):
    """Change ticket fields only if nobody else did first (the version check).
    Raises TicketChanged otherwise. The caller writes the history and commits."""
    values = {**values, "version": ticket.version + 1, "updated_at": now}
    result = s.execute(update(Ticket)
                       .where(Ticket.id == ticket.id, Ticket.version == ticket.version)
                       .values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        s.rollback()
        raise TicketChanged()
    # Our copy of the ticket now matches the row, without reading it again.
    for name, value in values.items():
        set_committed_value(ticket, name, value)


def touch(s, ticket, now, **values):
    """Times only (updated_at, first_response_at): no version check, because a
    reply changes nothing anyone else could be editing."""
    values = {**values, "updated_at": now}
    s.execute(update(Ticket).where(Ticket.id == ticket.id).values(**values)
              .execution_options(synchronize_session=False))
    for name, value in values.items():
        set_committed_value(ticket, name, value)


def move(s, ticket, to, actor, now, resolution="", note=""):
    """Move the ticket (the caller has checked permissions.can_move) and write the history."""
    problem = move_problem(ticket, to, resolution)
    if problem:
        raise MoveNotAllowed(problem)
    before = ticket.status
    values = {"status": to}
    if to == RESOLVED:
        values.update(resolved_at=now, resolution=resolution.strip())
    if before == RESOLVED and to == OPEN:
        values.update(resolved_at=None, resolution="")
    if to in FINISHED:
        values["closed_at"] = now
    # Waiting on the customer stops the "solved" clock (sla.py); coming back adds the
    # working minutes it was stopped to the limit.
    if to == WAITING:
        values["paused_since"] = now
    elif before == WAITING and ticket.paused_since is not None:
        desk = s.get(Desk, ticket.desk_id)
        paused = ticket.paused_minutes + hours.minutes_between(desk, ticket.paused_since, now, hours.holiday_days(s, desk))
        values.update(paused_since=None, paused_minutes=paused,
                      resolve_due_at=sla.targets(s, desk, ticket, paused_minutes=paused)["resolve_due_at"])
    apply(s, ticket, now, **values)
    audit.log(s, ticket.desk_id, actor, audit.STATUS, now, ticket=ticket, field="status", old=before, new=to,
              note=resolution.strip() if to == RESOLVED else note)
    notify.after_move(s, ticket, before, to, actor, now)
