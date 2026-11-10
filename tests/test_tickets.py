"""Ticket pages: sending, seeing, replying, assigning, and moving through the workflow."""
import re

import pytest
from sqlalchemy import select

from tickettock import db, tickets, workflow
from tickettock.dates import utcnow
from tickettock.models import AuditEvent, Message, Ticket, User

from .conftest import browser, get_ticket, make_team, make_ticket, post


@pytest.fixture
def team(app):
    return make_team(app)


def send(client, subject="Can't log in", description="It says my password is wrong.", **extra):
    return post(client, "/tickets/new", {"subject": subject, "description": description, "priority": "normal", **extra})


def page(client, url):
    return client.get(url).get_data(as_text=True)


def history(app, number):
    with app.app_context():
        s = db.session()
        ticket_id = s.scalar(select(Ticket.id).where(Ticket.number == number))
        return [(e.verb, e.field, e.old_value, e.new_value, e.actor_name)
                for e in s.scalars(select(AuditEvent).where(AuditEvent.ticket_id == ticket_id).order_by(AuditEvent.seq))]


# ---------- Sending and seeing ----------

def test_a_customer_sends_a_ticket(app, team):
    client = browser(app, team["customer"])
    response = send(client)
    assert response.status_code == 302 and response.location.endswith("/tickets/1")
    ticket = get_ticket(app, 1)
    assert ticket.status == "new" and ticket.customer_id == team["customer"] and ticket.number == 1
    assert "TT-1" in page(client, "/tickets")
    assert history(app, 1) == [("created", "", "", "Can't log in", "Maria Santos")]


def test_ticket_numbers_go_up_and_are_never_reused(app, team):
    client = browser(app, team["customer"])
    for _ in range(3):
        send(client)
    with app.app_context():
        assert db.session().scalars(select(Ticket.number).order_by(Ticket.number)).all() == [1, 2, 3]


def test_sending_checks_the_form(app, team):
    client = browser(app, team["customer"])
    response = send(client, subject="", description="", priority="urgent")
    text = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Say in a few words" in text and "Tell us what happened" in text and "Choose how much" in text


def test_staff_do_not_send_tickets(app, team):
    assert browser(app, team["agent"]).get("/tickets/new").status_code == 403


def test_a_customer_sees_only_their_own_tickets(app, team):
    mine = make_ticket(app, team["customer"], subject="Mine")
    theirs = make_ticket(app, team["other_customer"], subject="Someone else's")
    client = browser(app, team["customer"])
    assert client.get(f"/tickets/{mine}").status_code == 200
    assert client.get(f"/tickets/{theirs}").status_code == 404  # not 403: the number never leaks
    assert "Someone else" not in page(client, "/tickets")


def test_customers_cannot_open_the_queue(app, team):
    assert browser(app, team["customer"]).get("/queue").status_code == 403


@pytest.mark.parametrize("who", ["agent", "lead", "admin"])
def test_staff_see_every_ticket(app, team, who):
    number = make_ticket(app, team["customer"])
    assert browser(app, team[who]).get(f"/tickets/{number}").status_code == 200
    assert "Can&#39;t log in" in page(browser(app, team[who]), "/queue?tab=open")


def test_home_sends_customers_to_their_tickets(app, team):
    response = browser(app, team["customer"]).get("/")
    assert response.status_code == 302 and response.location.endswith("/tickets")
    assert "Unassigned" in page(browser(app, team["agent"]), "/")


# ---------- The queue ----------

def test_queue_tabs_search_and_filters(app, team):
    make_ticket(app, team["customer"], subject="Refund please", priority="high")
    make_ticket(app, team["customer"], subject="Mine now", assignee_id=team["agent"], status="open")
    make_ticket(app, team["other_customer"], subject="Old one", status="closed")
    client = browser(app, team["agent"])
    unassigned = page(client, "/queue?tab=unassigned")
    assert "Refund please" in unassigned and "Mine now" not in unassigned
    assert "Mine now" in page(client, "/queue?tab=mine")
    assert "Old one" in page(client, "/queue?tab=closed")
    assert "Refund please" in page(client, "/queue?tab=open&q=refund")
    assert "Refund please" not in page(client, "/queue?tab=open&q=TT-2")
    assert "Mine now" in page(client, "/queue?tab=open&q=TT-2")
    assert "Refund please" in page(client, "/queue?tab=open&q=maria")  # the customer's name
    assert "Refund please" not in page(client, "/queue?tab=open&q=jose")
    assert "Mine now" not in page(client, "/queue?tab=open&priority=high")


def test_urgent_comes_first(app, team):
    make_ticket(app, team["customer"], subject="Low one", priority="low")
    make_ticket(app, team["customer"], subject="Fire!", priority="urgent")
    text = page(browser(app, team["agent"]), "/queue")
    assert text.index("Fire!") < text.index("Low one")


# ---------- Replies ----------

def test_the_first_staff_reply_opens_a_new_ticket_and_counts_as_first_response(app, team):
    number = make_ticket(app, team["customer"])
    agent = browser(app, team["agent"])
    post(agent, f"/tickets/{number}/reply", {"body": "Let me check."})
    ticket = get_ticket(app, number)
    assert ticket.status == "open" and ticket.first_response_at is not None
    assert "Let me check." in page(browser(app, team["customer"]), f"/tickets/{number}")


def test_a_customer_reply_brings_a_waiting_ticket_back(app, team):
    number = make_ticket(app, team["customer"], status="waiting", assignee_id=team["agent"])
    post(browser(app, team["customer"]), f"/tickets/{number}/reply", {"body": "Here is the screenshot."})
    assert get_ticket(app, number).status == "in_progress"
    assert ("status", "status", "waiting", "in_progress", "Ticket Tock") in history(app, number)


def test_a_customer_reply_reopens_a_resolved_ticket_within_seven_days(app, team):
    number = make_ticket(app, team["customer"], status="resolved", assignee_id=team["agent"],
                         resolution="Reset it.", resolved_at=utcnow())
    post(browser(app, team["customer"]), f"/tickets/{number}/reply", {"body": "Still broken."})
    ticket = get_ticket(app, number)
    assert ticket.status == "open" and ticket.resolution == "" and ticket.resolved_at is None


def test_send_and_wait_for_customer(app, team):
    number = make_ticket(app, team["customer"], status="in_progress", assignee_id=team["agent"])
    post(browser(app, team["agent"]), f"/tickets/{number}/reply", {"body": "Which phone?", "then": "waiting"})
    assert get_ticket(app, number).status == "waiting"


def test_no_replies_on_closed_tickets(app, team):
    number = make_ticket(app, team["customer"], status="closed")
    assert post(browser(app, team["customer"]), f"/tickets/{number}/reply", {"body": "Hi"}).status_code == 403
    assert post(browser(app, team["agent"]), f"/tickets/{number}/reply", {"body": "Hi"}).status_code == 403


def test_another_customer_cannot_reply(app, team):
    number = make_ticket(app, team["customer"])
    response = post(browser(app, team["other_customer"]), f"/tickets/{number}/reply", {"body": "Hi"})
    assert response.status_code == 404


# ---------- Assigning, priority, category ----------

def version(app, number):
    return str(get_ticket(app, number).version)


def test_take_it_makes_the_ticket_yours_and_opens_it(app, team):
    number = make_ticket(app, team["customer"])
    agent = browser(app, team["agent"])
    post(agent, f"/tickets/{number}/take", {"version": version(app, number)})
    ticket = get_ticket(app, number)
    assert ticket.assignee_id == team["agent"] and ticket.status == "open"
    assert ("assigned", "assignee", "", "Ana Reyes", "Ana Reyes") in history(app, number)
    # Now it is taken, another agent can't take it.
    other = browser(app, team["other_agent"])
    assert post(other, f"/tickets/{number}/take", {"version": version(app, number)}).status_code == 403


def test_hand_back(app, team):
    number = make_ticket(app, team["customer"], status="open", assignee_id=team["agent"])
    post(browser(app, team["agent"]), f"/tickets/{number}/drop", {"version": version(app, number)})
    assert get_ticket(app, number).assignee_id is None


@pytest.mark.parametrize("who, status", [("agent", 403), ("lead", 302), ("admin", 302), ("customer", 403)])
def test_only_leads_assign_to_others(app, team, who, status):
    number = make_ticket(app, team["customer"])
    response = post(browser(app, team[who]), f"/tickets/{number}/assign",
                    {"assignee_id": team["other_agent"], "version": version(app, number)})
    assert response.status_code == status


def test_tickets_go_only_to_active_staff(app, team):
    number = make_ticket(app, team["customer"])
    lead = browser(app, team["lead"])
    response = post(lead, f"/tickets/{number}/assign", {"assignee_id": team["customer"], "version": version(app, number)})
    assert response.status_code == 400


def test_priority_and_category_changes_are_in_the_history(app, team):
    number = make_ticket(app, team["customer"])
    agent = browser(app, team["agent"])
    post(agent, f"/tickets/{number}/priority", {"priority": "urgent", "version": version(app, number)})
    with app.app_context():
        category_id = db.session().scalar(select(tickets.Category.id).where(tickets.Category.name == "Billing"))
    post(agent, f"/tickets/{number}/category", {"category_id": category_id, "version": version(app, number)})
    ticket = get_ticket(app, number)
    assert ticket.priority == "urgent" and ticket.category_id == category_id
    log = history(app, number)
    assert ("priority", "priority", "normal", "urgent", "Ana Reyes") in log
    assert ("category", "category", "", "Billing", "Ana Reyes") in log


def test_an_agent_cannot_reprioritize_someone_elses_ticket(app, team):
    number = make_ticket(app, team["customer"], status="open", assignee_id=team["other_agent"])
    response = post(browser(app, team["agent"]), f"/tickets/{number}/priority",
                    {"priority": "low", "version": version(app, number)})
    assert response.status_code == 403


# ---------- Moving ----------

def move(client, number, to, app, **extra):
    return post(client, f"/tickets/{number}/move", {"to": to, "version": version(app, number), **extra})


def test_the_whole_road_from_new_to_closed(app, team):
    number = make_ticket(app, team["customer"])
    agent, customer = browser(app, team["agent"]), browser(app, team["customer"])
    post(agent, f"/tickets/{number}/take", {"version": version(app, number)})
    move(agent, number, "in_progress", app)
    move(agent, number, "waiting", app)
    post(customer, f"/tickets/{number}/reply", {"body": "Done that."})
    move(agent, number, "resolved", app, resolution="Cleared the cache.")
    assert "How It Was Solved" in page(customer, f"/tickets/{number}")
    move(customer, number, "closed", app)
    ticket = get_ticket(app, number)
    assert ticket.status == "closed" and ticket.closed_at and ticket.resolution == "Cleared the cache."
    statuses = [(old, new) for verb, field, old, new, _ in history(app, number) if verb == "status"]
    assert statuses == [("new", "open"), ("open", "in_progress"), ("in_progress", "waiting"),
                        ("waiting", "in_progress"), ("in_progress", "resolved"), ("resolved", "closed")]


def test_a_refused_move_explains_why(app, team):
    number = make_ticket(app, team["customer"], status="open")
    lead = browser(app, team["lead"])
    response = move(lead, number, "in_progress", app)
    assert response.status_code == 302
    assert "Give the ticket to someone first." in page(lead, f"/tickets/{number}")
    assert get_ticket(app, number).status == "open"


def test_resolving_needs_words(app, team):
    number = make_ticket(app, team["customer"], status="in_progress", assignee_id=team["agent"])
    move(browser(app, team["agent"]), number, "resolved", app, resolution="  ")
    assert get_ticket(app, number).status == "in_progress"


def test_an_agent_cannot_move_someone_elses_ticket(app, team):
    number = make_ticket(app, team["customer"], status="open", assignee_id=team["other_agent"])
    assert move(browser(app, team["agent"]), number, "in_progress", app).status_code == 403


def test_a_customer_cancels_before_work_starts(app, team):
    number = make_ticket(app, team["customer"])
    move(browser(app, team["customer"]), number, "cancelled", app)
    assert get_ticket(app, number).status == "cancelled"


def test_a_customer_cannot_resolve_their_own_ticket(app, team):
    number = make_ticket(app, team["customer"], status="open", assignee_id=team["agent"])
    assert move(browser(app, team["customer"]), number, "resolved", app, resolution="x").status_code == 403


def test_an_unknown_status_is_a_bad_request(app, team):
    number = make_ticket(app, team["customer"])
    assert move(browser(app, team["lead"]), number, "deleted", app).status_code == 400


# ---------- Two people at once ----------

def test_a_form_from_an_old_page_is_refused(app, team):
    number = make_ticket(app, team["customer"])
    lead = browser(app, team["lead"])
    old = version(app, number)
    post(lead, f"/tickets/{number}/priority", {"priority": "high", "version": old})
    post(lead, f"/tickets/{number}/priority", {"priority": "low", "version": old})  # drawn before the change
    assert get_ticket(app, number).priority == "high"
    assert "changed while you were looking" in page(lead, f"/tickets/{number}")


def test_the_version_check_stops_a_lost_update(app, team):
    """Two people read the ticket, then both write. The second write must fail, not overwrite."""
    number = make_ticket(app, team["customer"])
    with app.app_context():
        s1 = db.session()
        first = s1.scalar(select(Ticket).where(Ticket.number == number))
        lead = s1.get(User, team["lead"])
        # Someone else changes it in another session, after we read it.
        with db.Session(db.engine()) as s2:
            second = s2.scalar(select(Ticket).where(Ticket.number == number))
            second.priority, second.version = "low", second.version + 1
            s2.commit()
        with pytest.raises(workflow.TicketChanged):
            tickets.set_priority(s1, first, lead, "urgent", utcnow())
    assert get_ticket(app, number).priority == "low"


def test_internal_messages_never_reach_the_customer(app, team):
    number = make_ticket(app, team["customer"])
    with app.app_context():
        s = db.session()
        ticket = s.scalar(select(Ticket).where(Ticket.number == number))
        tickets.reply(s, ticket, s.get(User, team["agent"]), "Customer seems confused.", utcnow(), internal=True)
        s.commit()
        assert s.scalar(select(Message.is_internal)) is True
    assert "seems confused" not in page(browser(app, team["customer"]), f"/tickets/{number}")
    assert "seems confused" in page(browser(app, team["agent"]), f"/tickets/{number}")


def test_the_tab_for_your_own_tickets_is_called_assigned_to_me(app, team):
    agent = browser(app, team["agent"])
    assert "Assigned to Me" in page(agent, "/queue")
    assert "Assigned to Me" in page(agent, "/")


def test_queue_tabs_follow_the_workflow_and_open_on_all_open(app, team):
    text = page(browser(app, team["agent"]), "/queue")
    order = [text.index(name) for name in ("All Open", "Unassigned", "Assigned to Me", "Overdue", "Waiting",
                                           "Resolved", "Closed")]
    assert order == sorted(order)
    assert re.search(r'aria-current="page">\s*All Open', text)  # the tab it opens on
