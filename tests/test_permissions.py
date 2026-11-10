"""Every rule in permissions.py as a table: each role against each action.

The rules are plain functions of users (and tickets), so these tests need no
database. The route tests (test_admin.py, test_tickets.py, ...) check that
the views really ask them.
"""
from types import SimpleNamespace

import pytest

from tickettock import permissions as can
from tickettock.models import ADMIN, AGENT, CUSTOMER, LEAD, STAFF_ROLES

ROLES = [CUSTOMER, AGENT, LEAD, ADMIN]
ME = 1


def person(role, id=ME, active=True, desk_id=1):
    return SimpleNamespace(id=id, role=role, is_active=active, desk_id=desk_id, is_staff=role in STAFF_ROLES)


#  rule                                Customer Agent  Lead   Admin
DESK_TABLE = [
    (can.is_staff,                     [False,  True,  True,  True]),
    (can.is_lead,                      [False,  False, True,  True]),
    (can.is_admin,                     [False,  False, False, True]),
    (can.can_manage_staff,             [False,  False, False, True]),
    (can.can_manage_categories,        [False,  False, False, True]),
]


@pytest.mark.parametrize("rule, expected", DESK_TABLE, ids=[rule.__name__ for rule, _ in DESK_TABLE])
def test_desk_rules(rule, expected):
    assert [rule(person(role)) for role in ROLES] == expected


@pytest.mark.parametrize("rule", [rule for rule, _ in DESK_TABLE])
def test_a_switched_off_account_may_do_nothing(rule):
    assert rule(person(ADMIN, active=False)) is False


def test_nobody_logged_in_may_do_nothing():
    assert not any(rule(None) for rule, _ in DESK_TABLE)


#  whose role is changed                  Customer Agent  Lead   Admin  (by an Admin)
def test_an_admin_changes_other_staff_roles_only():
    admin = person(ADMIN)
    assert [can.can_change_role(admin, person(role, id=2)) for role in ROLES] == [False, True, True, True]


def test_nobody_changes_their_own_role():
    admin = person(ADMIN)
    assert can.can_change_role(admin, admin) is False
    assert can.can_switch_off(admin, admin) is False


def test_only_admins_change_roles():
    for role in (CUSTOMER, AGENT, LEAD):
        assert can.can_change_role(person(role), person(AGENT, id=2)) is False


def test_never_across_desks():
    assert can.can_change_role(person(ADMIN), person(AGENT, id=2, desk_id=99)) is False


def test_roles_an_admin_can_give():
    assert can.roles_you_can_give(person(ADMIN)) == (AGENT, LEAD, ADMIN)
    assert can.roles_you_can_give(person(LEAD)) == ()
