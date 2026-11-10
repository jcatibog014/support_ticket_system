"""Closing resolved tickets that nobody reopened.

A customer can reopen a resolved ticket for 7 days (permissions.REOPEN_WINDOW).
After that the timer (tick.py) closes it, as "Ticket Tock", through the normal
workflow, so it has a history line and the customer gets a notice like any move.
"""
from sqlalchemy import select

from . import workflow
from .models import CLOSED, RESOLVED, Ticket
from .permissions import REOPEN_WINDOW

NOTE = "Closed by itself 7 days after it was resolved."


def close_old(s, desk, now):
    """Returns how many were closed."""
    old = s.scalars(select(Ticket).where(Ticket.desk_id == desk.id, Ticket.status == RESOLVED,
                                         Ticket.resolved_at <= now - REOPEN_WINDOW).order_by(Ticket.id)).all()
    closed = 0
    for ticket in old:
        try:
            workflow.move(s, ticket, CLOSED, None, now, note=NOTE)
            s.commit()
            closed += 1
        except workflow.TicketChanged:
            continue  # someone touched it just now; the next run looks again
    return closed
