"""The workflow as two tables: what the TICKET allows (move_problem), and what each
PERSON may do (permissions.can_move). Neither needs a database."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tickettock import permissions as can
from tickettock import workflow
from tickettock.models import (ADMIN, AGENT, CANCELLED, CLOSED, CUSTOMER, IN_PROGRESS, LEAD, NEW, OPEN, RESOLVED,
                               STAFF_ROLES, STATUSES, WAITING)

NOW = datetime(2026, 11, 10, 9, 0)


def ticket(status, assignee_id=None, customer_id=10, resolved_days_ago=None):
    return SimpleNamespace(id=1, desk_id=1, status=status, assignee_id=assignee_id, customer_id=customer_id,
                           resolved_at=NOW - timedelta(days=resolved_days_ago) if resolved_days_ago is not None else None)


def person(role, id):
    return SimpleNamespace(id=id, role=role, is_active=True, desk_id=1, is_staff=role in STAFF_ROLES)


# ---------- The ticket's side: every status against every other ----------

ALLOWED = {
    NEW: {OPEN, IN_PROGRESS, WAITING, RESOLVED, CANCELLED},
    OPEN: {IN_PROGRESS, WAITING, RESOLVED, CANCELLED},
    IN_PROGRESS: {OPEN, WAITING, RESOLVED},
    WAITING: {IN_PROGRESS, RESOLVED},
    RESOLVED: {OPEN, CLOSED},
    CLOSED: set(),
    CANCELLED: set(),
}


@pytest.mark.parametrize("before", STATUSES)
@pytest.mark.parametrize("to", STATUSES)
def test_every_move_in_the_table(before, to):
    """With an assignee and a resolution, a move is allowed exactly when the table says so."""
    problem = workflow.move_problem(ticket(before, assignee_id=1), to, resolution="Reset the password.")
    assert (problem is None) == (to in ALLOWED[before]), problem


def test_closed_and_cancelled_are_final():
    for status in (CLOSED, CANCELLED):
        assert workflow.TRANSITIONS[status] == ()


@pytest.mark.parametrize("to", [IN_PROGRESS, WAITING])
def test_work_needs_someone_on_the_ticket(to):
    assert workflow.move_problem(ticket(OPEN), to) == "Give the ticket to someone first."


def test_resolving_needs_a_resolution():
    assert "Write how it was solved" in workflow.move_problem(ticket(OPEN, assignee_id=1), RESOLVED, "   ")
    assert workflow.move_problem(ticket(OPEN, assignee_id=1), RESOLVED, "x" * 2001) is not None


def test_a_move_to_the_same_status_is_refused():
    assert workflow.move_problem(ticket(OPEN), OPEN) == "It is already Open."


# ---------- The person's side ----------

CUSTOMER_ID, AGENT_ID, OTHER_AGENT_ID, LEAD_ID = 10, 20, 21, 30
PEOPLE = {
    "owner": person(CUSTOMER, CUSTOMER_ID),
    "other customer": person(CUSTOMER, 11),
    "assigned agent": person(AGENT, AGENT_ID),
    "other agent": person(AGENT, OTHER_AGENT_ID),
    "lead": person(LEAD, LEAD_ID),
    "admin": person(ADMIN, 40),
}

#  (status, to)                   owner  other  assigned other  lead   admin
#                                        cust.  agent    agent
MOVE_TABLE = [
    ((NEW, OPEN),                 [False, False, True,    False, True,  True]),
    ((OPEN, IN_PROGRESS),         [False, False, True,    False, True,  True]),
    ((IN_PROGRESS, WAITING),      [False, False, True,    False, True,  True]),
    ((WAITING, IN_PROGRESS),      [False, False, True,    False, True,  True]),
    ((IN_PROGRESS, RESOLVED),     [False, False, True,    False, True,  True]),
    # The customer cancels before work starts; afterwards only a Lead can (spam).
    ((NEW, CANCELLED),            [True,  False, False,   False, True,  True]),
    ((OPEN, CANCELLED),           [True,  False, False,   False, True,  True]),
    ((IN_PROGRESS, CANCELLED),    [False, False, False,   False, True,  True]),
    # The customer closes a resolved ticket ("it's fixed"), or reopens it.
    ((RESOLVED, CLOSED),          [True,  False, False,   False, True,  True]),
    ((RESOLVED, OPEN),            [True,  False, True,    False, True,  True]),
]


@pytest.mark.parametrize("move, expected", MOVE_TABLE, ids=[f"{a}->{b}" for (a, b), _ in MOVE_TABLE])
def test_who_may_move(move, expected):
    before, to = move
    subject = ticket(before, assignee_id=AGENT_ID, resolved_days_ago=1 if before == RESOLVED else None)
    assert [can.can_move(person_, subject, to, NOW) for person_ in PEOPLE.values()] == expected


def test_reopening_ends_after_seven_days():
    owner = PEOPLE["owner"]
    assert can.can_move(owner, ticket(RESOLVED, resolved_days_ago=6), OPEN, NOW)
    assert not can.can_move(owner, ticket(RESOLVED, resolved_days_ago=8), OPEN, NOW)
    assert can.can_reply(owner, ticket(RESOLVED, resolved_days_ago=6), NOW)
    assert not can.can_reply(owner, ticket(RESOLVED, resolved_days_ago=8), NOW)


def test_nobody_replies_to_a_closed_ticket():
    for person_ in PEOPLE.values():
        assert not can.can_reply(person_, ticket(CLOSED, assignee_id=AGENT_ID), NOW)


def test_customers_never_see_other_peoples_tickets():
    assert can.can_view_ticket(PEOPLE["owner"], ticket(OPEN))
    assert not can.can_view_ticket(PEOPLE["other customer"], ticket(OPEN))
    assert all(can.can_view_ticket(PEOPLE[who], ticket(OPEN)) for who in ("other agent", "lead", "admin"))


def test_nobody_sees_another_desks_ticket():
    other_desk = SimpleNamespace(**{**vars(ticket(OPEN)), "desk_id": 2})
    assert not any(can.can_view_ticket(person_, other_desk) for person_ in PEOPLE.values())


#  rule                     owner  other  assigned other  lead   admin    (on an OPEN ticket of the assigned agent)
TICKET_TABLE = [
    (can.can_assign,        [False, False, False,   False, True,  True]),
    (can.can_drop,          [False, False, True,    False, False, False]),
    (can.can_triage,        [False, False, True,    False, True,  True]),
    (can.can_take,          [False, False, False,   False, False, False]),  # it already has someone
]


@pytest.mark.parametrize("rule, expected", TICKET_TABLE, ids=[rule.__name__ for rule, _ in TICKET_TABLE])
def test_ticket_rules(rule, expected):
    assert [rule(person_, ticket(OPEN, assignee_id=AGENT_ID)) for person_ in PEOPLE.values()] == expected


def test_any_staff_member_takes_or_sorts_an_unassigned_ticket():
    unassigned = ticket(NEW)
    for who in ("assigned agent", "other agent", "lead", "admin"):
        assert can.can_take(PEOPLE[who], unassigned) and can.can_triage(PEOPLE[who], unassigned)
    assert not can.can_take(PEOPLE["owner"], unassigned)


def test_finished_tickets_cannot_be_changed():
    done = ticket(CLOSED, assignee_id=AGENT_ID)
    for rule in (can.can_assign, can.can_drop, can.can_triage, can.can_take):
        assert not any(rule(person_, done) for person_ in PEOPLE.values())
