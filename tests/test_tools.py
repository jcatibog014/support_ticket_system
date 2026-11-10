"""Stage 5: saved replies, saved views, bulk changes, round-robin, "also looking", and merging."""
import pytest
from sqlalchemy import select

from tickettock import db
from tickettock.models import AuditEvent, Category, Message, Notification, SavedReply, SavedView

from .conftest import browser, get_ticket, make_team, make_ticket, make_user, post


@pytest.fixture
def team(app):
    return make_team(app)


def page(client, url):
    return client.get(url).get_data(as_text=True)


def version(app, number):
    return str(get_ticket(app, number).version)


# ---------- Saved replies ----------

def add_reply(app, team, title="Password reset", body="Hi {name}, I reset it for you on {ticket}. {agent}"):
    return post(browser(app, team["lead"]), "/saved-replies", {"title": title, "body": body})


def test_team_leads_keep_saved_replies(app, team):
    assert add_reply(app, team).status_code == 302
    assert add_reply(app, team).status_code == 400  # same title
    assert post(browser(app, team["agent"]), "/saved-replies", {"title": "Mine", "body": "x"}).status_code == 403
    assert browser(app, team["agent"]).get("/saved-replies").status_code == 200  # agents read them
    assert browser(app, team["customer"]).get("/saved-replies").status_code == 403


def test_a_saved_reply_is_filled_in_on_the_ticket(app, team):
    add_reply(app, team)
    number = make_ticket(app, team["customer"])
    with app.app_context():
        reply_id = db.session().scalar(select(SavedReply.id))
    text = page(browser(app, team["agent"]), f"/tickets/{number}?use={reply_id}")
    assert f"Hi Maria, I reset it for you on TT-{number}. Ana" in text


def test_customers_never_see_saved_replies(app, team):
    add_reply(app, team)
    number = make_ticket(app, team["customer"])
    assert "Password reset" not in page(browser(app, team["customer"]), f"/tickets/{number}")


def test_editing_and_deleting_saved_replies(app, team):
    add_reply(app, team)
    with app.app_context():
        reply_id = db.session().scalar(select(SavedReply.id))
    lead = browser(app, team["lead"])
    post(lead, f"/saved-replies/{reply_id}", {"title": "Reset", "body": "Done, {name}."})
    with app.app_context():
        assert db.session().get(SavedReply, reply_id).title == "Reset"
    assert post(browser(app, team["agent"]), f"/saved-replies/{reply_id}", {"action": "delete"}).status_code == 403
    post(lead, f"/saved-replies/{reply_id}", {"action": "delete"})
    with app.app_context():
        assert db.session().get(SavedReply, reply_id) is None
    assert "Deleted the saved reply Reset" in page(browser(app, team["admin"]), "/audit")


# ---------- Saved views ----------

def test_saved_views_keep_only_queue_filters(app, team):
    agent = browser(app, team["agent"])
    post(agent, "/queue/views", {"name": "Urgent open", "tab": "open", "priority": "urgent", "evil": "x"})
    with app.app_context():
        view = db.session().scalar(select(SavedView))
    assert view.query == "tab=open&priority=urgent"
    assert "Urgent open" in page(agent, "/queue")
    assert "Urgent open" not in page(browser(app, team["other_agent"]), "/queue")  # each person's own


def test_nobody_deletes_someone_elses_view(app, team):
    post(browser(app, team["agent"]), "/queue/views", {"name": "Mine", "tab": "mine"})
    with app.app_context():
        view_id = db.session().scalar(select(SavedView.id))
    assert post(browser(app, team["other_agent"]), f"/queue/views/{view_id}/delete").status_code == 404
    post(browser(app, team["agent"]), f"/queue/views/{view_id}/delete")
    with app.app_context():
        assert db.session().get(SavedView, view_id) is None


# ---------- Bulk changes ----------

def test_a_team_lead_assigns_many_tickets_at_once(app, team):
    numbers = [make_ticket(app, team["customer"]) for _ in range(3)]
    lead = browser(app, team["lead"])
    post(lead, "/queue/bulk", {"ticket": [str(n) for n in numbers], "action": "assign", "assignee_id": str(team["agent"])})
    assert all(get_ticket(app, n).assignee_id == team["agent"] for n in numbers)
    assert "Changed 3 of 3 tickets." in page(lead, "/queue")


def test_bulk_skips_what_it_may_not_change(app, team):
    resolved = make_ticket(app, team["customer"], status="resolved", resolution="Done.")
    still_open = make_ticket(app, team["customer"], status="open")
    lead = browser(app, team["lead"])
    post(lead, "/queue/bulk", {"ticket": [str(resolved), str(still_open)], "action": "close"})
    assert get_ticket(app, resolved).status == "closed" and get_ticket(app, still_open).status == "open"
    assert "1 could not be changed" in page(lead, "/queue")


def test_bulk_priority(app, team):
    numbers = [make_ticket(app, team["customer"]) for _ in range(2)]
    post(browser(app, team["admin"]), "/queue/bulk", {"ticket": [str(n) for n in numbers], "action": "priority", "priority": "high"})
    assert {get_ticket(app, n).priority for n in numbers} == {"high"}


def test_agents_cannot_change_many_at_once(app, team):
    number = make_ticket(app, team["customer"])
    response = post(browser(app, team["agent"]), "/queue/bulk", {"ticket": [str(number)], "action": "priority", "priority": "low"})
    assert response.status_code == 403


# ---------- Round-robin ----------

def test_auto_assign_gives_tickets_to_agents_in_turn(app, team):
    admin = browser(app, team["admin"])
    with app.app_context():
        billing = db.session().scalar(select(Category).where(Category.name == "Billing"))
    post(admin, f"/admin/categories/{billing.id}", {"action": "auto_on"})
    customer = browser(app, team["customer"])
    for _ in range(3):
        post(customer, "/tickets/new", {"subject": "Bill", "description": "Help", "priority": "normal",
                                        "category": str(billing.id)})
    assignees = [get_ticket(app, n).assignee_id for n in (1, 2, 3)]
    assert assignees == [team["agent"], team["other_agent"], team["agent"]]  # Agents only, in turn
    assert get_ticket(app, 1).status == "open"
    assert "Ticket Tock gave it to Ana Reyes" in page(browser(app, team["lead"]), "/tickets/1")


def test_switched_off_agents_get_no_tickets(app, team):
    admin = browser(app, team["admin"])
    post(admin, f"/admin/staff/{team['agent']}/active", {"active": "0"})
    with app.app_context():
        billing = db.session().scalar(select(Category).where(Category.name == "Billing"))
    post(admin, f"/admin/categories/{billing.id}", {"action": "auto_on"})
    post(browser(app, team["customer"]), "/tickets/new",
         {"subject": "Bill", "description": "Help", "priority": "normal", "category": str(billing.id)})
    assert get_ticket(app, 1).assignee_id == team["other_agent"]


def test_without_auto_assign_tickets_wait_in_the_queue(app, team):
    post(browser(app, team["customer"]), "/tickets/new", {"subject": "Hi", "description": "Help", "priority": "normal"})
    assert get_ticket(app, 1).assignee_id is None


# ---------- Also looking ----------

def test_staff_see_who_else_is_looking(app, team):
    number = make_ticket(app, team["customer"])
    browser(app, team["other_agent"]).get(f"/tickets/{number}")
    agent = browser(app, team["agent"])
    assert "Carlo Diaz is also looking at this ticket." in page(agent, f"/tickets/{number}")
    answer = agent.get(f"/tickets/{number}/looking").get_json()
    assert answer == {"names": ["Carlo Diaz"]}


def test_customers_are_never_counted_or_told(app, team):
    number = make_ticket(app, team["customer"])
    browser(app, team["agent"]).get(f"/tickets/{number}")
    customer = browser(app, team["customer"])
    assert "also looking" not in page(customer, f"/tickets/{number}")
    assert customer.get(f"/tickets/{number}/looking").status_code == 403


# ---------- Merging ----------

def test_a_team_lead_merges_a_duplicate(app, team):
    keep = make_ticket(app, team["customer"], subject="Can't log in")
    duplicate = make_ticket(app, team["customer"], subject="Login broken again")
    lead = browser(app, team["lead"])
    response = post(lead, f"/tickets/{duplicate}/merge", {"into": f"TT-{keep}", "version": version(app, duplicate)})
    assert response.location.endswith(f"/tickets/{keep}")
    gone = get_ticket(app, duplicate)
    assert gone.status == "closed" and gone.merged_into_id == get_ticket(app, keep).id
    with app.app_context():
        s = db.session()
        note = s.scalar(select(Message).where(Message.ticket_id == get_ticket(app, keep).id))
        verbs = set(s.scalars(select(AuditEvent.verb)))
        told = s.scalar(select(Notification.kind).where(Notification.user_id == team["customer"]))
    assert note.is_internal and "Login broken again" in note.body
    assert {"merged", "merged_here"} <= verbs and told == "merged"
    assert f"Merged into" in page(browser(app, team["customer"]), f"/tickets/{duplicate}")


def test_merging_needs_the_same_customer(app, team):
    keep = make_ticket(app, team["other_customer"])
    duplicate = make_ticket(app, team["customer"])
    post(browser(app, team["lead"]), f"/tickets/{duplicate}/merge", {"into": f"TT-{keep}", "version": version(app, duplicate)})
    assert get_ticket(app, duplicate).status == "new"


def test_agents_cannot_merge(app, team):
    keep = make_ticket(app, team["customer"])
    duplicate = make_ticket(app, team["customer"])
    response = post(browser(app, team["agent"]), f"/tickets/{duplicate}/merge",
                    {"into": f"TT-{keep}", "version": version(app, duplicate)})
    assert response.status_code == 403


def test_a_ticket_cannot_merge_into_itself(app, team):
    number = make_ticket(app, team["customer"])
    post(browser(app, team["lead"]), f"/tickets/{number}/merge", {"into": f"TT-{number}", "version": version(app, number)})
    assert get_ticket(app, number).status == "new"
