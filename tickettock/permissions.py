"""EVERY rule about who may do what lives here.

There are four roles, stored on the user (models.ROLES):

    Customer   signs up, sends tickets, and sees ONLY their own
    Agent      works the queue: takes tickets, replies, moves the ones they own
    Team Lead  an Agent who can also give any ticket to anyone, and see the Audit Log
    Admin      everything, plus the staff, categories and desk settings

Views and templates never compare roles themselves: they call these plain
functions (in a template: can.can_manage_staff(g.user)). So
tests/test_permissions.py can check every rule as a table of role x action.

A switched-off account (is_active False) may do nothing at all.
"""
from datetime import timedelta

from .dates import utcnow
from .models import (ACTIVE, ADMIN, AGENT, CANCELLED, CLOSED, CUSTOMER, FINISHED, IN_PROGRESS, LEAD, NEW, OPEN, RANK,
                     RESOLVED, STAFF_ROLES, WAITING)

# A customer can reopen a resolved ticket (or reply to it) for this long.
# After that it is closed, and a new ticket is the way to go.
REOPEN_WINDOW = timedelta(days=7)


def _active(user):
    return user is not None and user.is_active


def at_least(user, lowest):
    return _active(user) and RANK[user.role] >= RANK[lowest]


def is_staff(user):
    return at_least(user, AGENT)


def is_lead(user):
    """Team Leads and Admins."""
    return at_least(user, LEAD)


def is_admin(user):
    return at_least(user, ADMIN)


# ---------- The desk ----------

def can_manage_staff(user):
    """Invite staff, change their roles, switch accounts off."""
    return is_admin(user)


def roles_you_can_give(user):
    return STAFF_ROLES if can_manage_staff(user) else ()


def can_change_role(user, other):
    """An Admin changes another staff member's role. Never their own: the desk
    would lose its last Admin if they made themselves an Agent by mistake."""
    return (can_manage_staff(user) and other is not None and other.id != user.id
            and other.desk_id == user.desk_id and other.is_staff)


def can_switch_off(user, other):
    """Switch a staff account off (they can't log in; their history stays)."""
    return can_change_role(user, other)


def can_manage_categories(user):
    return is_admin(user)


# ---------- Tickets ----------

def _owns(user, ticket):
    return user.role == CUSTOMER and ticket.customer_id == user.id


def _mine(user, ticket):
    return ticket.assignee_id is not None and ticket.assignee_id == user.id


def can_view_ticket(user, ticket):
    """Staff see every ticket on their desk. A customer sees only their own
    (anyone else's is "not found", so its number never leaks)."""
    if not _active(user) or ticket.desk_id != user.desk_id:
        return False
    return is_staff(user) or _owns(user, ticket)


def reopen_window_open(ticket, now=None):
    now = now or utcnow()
    return ticket.status == RESOLVED and ticket.resolved_at is not None and now < ticket.resolved_at + REOPEN_WINDOW


def can_reply(user, ticket, now=None):
    """Public replies. Nobody writes on a closed ticket; a customer can still
    answer a resolved one for 7 days (which reopens it)."""
    if not can_view_ticket(user, ticket) or ticket.status in FINISHED:
        return False
    if _owns(user, ticket) and ticket.status == RESOLVED:
        return reopen_window_open(ticket, now)
    return True


def can_take(user, ticket):
    """"Take it": a staff member gives an unassigned, unfinished ticket to themselves."""
    return is_staff(user) and can_view_ticket(user, ticket) and ticket.assignee_id is None and ticket.status in ACTIVE


def can_assign(user, ticket):
    """Give the ticket to anyone on the team (or to nobody): Team Leads and Admins."""
    return is_lead(user) and can_view_ticket(user, ticket) and ticket.status not in FINISHED


def can_drop(user, ticket):
    """An Agent hands back a ticket they were given (it goes back to the queue)."""
    return is_staff(user) and can_view_ticket(user, ticket) and _mine(user, ticket) and ticket.status in ACTIVE


def can_triage(user, ticket):
    """Change the priority and category. A Lead on any ticket; an Agent on
    their own tickets and on unassigned ones (sorting the queue)."""
    if not (is_staff(user) and can_view_ticket(user, ticket)) or ticket.status in FINISHED:
        return False
    return is_lead(user) or _mine(user, ticket) or ticket.assignee_id is None


def can_move(user, ticket, to, now=None):
    """May this person make this move? (Whether the ticket is ready for it is workflow.move_problem().)"""
    if not can_view_ticket(user, ticket):
        return False
    if _owns(user, ticket):
        return ((to == CANCELLED and ticket.status in (NEW, OPEN))
                or (to == CLOSED and ticket.status == RESOLVED)
                or (to == OPEN and reopen_window_open(ticket, now)))
    if is_lead(user):
        return True
    # Agents work their own tickets. Closing and cancelling are for Team Leads
    # (the customer closes their own ticket by saying it's fixed).
    return is_staff(user) and _mine(user, ticket) and to in (OPEN, IN_PROGRESS, WAITING, RESOLVED)


def can_add_note(user, ticket):
    """Internal notes: the team talking among themselves, on any ticket, even a closed one."""
    return is_staff(user) and can_view_ticket(user, ticket)


# ---------- The history ----------

def can_view_audit_log(user):
    """The whole desk's history: Team Leads and Admins. (Everyone sees their own ticket's timeline.)"""
    return is_lead(user)


def can_export_audit_log(user):
    """Download it as a CSV file: Admins only, since it holds every customer's email."""
    return is_admin(user)
