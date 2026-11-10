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
from .models import ADMIN, AGENT, LEAD, RANK, STAFF_ROLES


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
