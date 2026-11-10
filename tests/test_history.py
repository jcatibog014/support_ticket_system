"""Stage 3: timelines, internal notes, the Audit Log page, the chain check, and the Inbox."""
import pytest
from sqlalchemy import select, update

from tickettock import db, mailer
from tickettock.models import AuditEvent, Notification, User

from .conftest import browser, make_team, make_ticket, post


@pytest.fixture
def team(app):
    return make_team(app)


def page(client, url):
    return client.get(url).get_data(as_text=True)


def version(app, number):
    from .conftest import get_ticket
    return str(get_ticket(app, number).version)


# ---------- Internal notes and the timeline ----------

def test_staff_add_internal_notes_that_customers_never_see(app, team):
    number = make_ticket(app, team["customer"])
    agent = browser(app, team["agent"])
    post(agent, f"/tickets/{number}/reply", {"body": "Looks like a cache problem.", "internal": "1"})
    customer_page = page(browser(app, team["customer"]), f"/tickets/{number}")
    assert "cache problem" not in customer_page
    assert "internal note" not in customer_page  # not even the timeline line
    staff_page = page(agent, f"/tickets/{number}")
    assert "cache problem" in staff_page and "added an internal note" in staff_page


def test_customers_cannot_write_internal_notes(app, team):
    number = make_ticket(app, team["customer"])
    response = post(browser(app, team["customer"]), f"/tickets/{number}/reply", {"body": "Psst", "internal": "1"})
    assert response.status_code == 403


def test_notes_are_allowed_on_closed_tickets(app, team):
    number = make_ticket(app, team["customer"], status="closed")
    response = post(browser(app, team["agent"]), f"/tickets/{number}/reply", {"body": "For the record.", "internal": "1"})
    assert response.status_code == 302


def test_a_note_does_not_count_as_a_first_response(app, team):
    from .conftest import get_ticket
    number = make_ticket(app, team["customer"])
    post(browser(app, team["agent"]), f"/tickets/{number}/reply", {"body": "Hmm.", "internal": "1"})
    ticket = get_ticket(app, number)
    assert ticket.first_response_at is None and ticket.status == "new"


def test_the_timeline_tells_the_story(app, team):
    number = make_ticket(app, team["customer"])
    agent = browser(app, team["agent"])
    post(agent, f"/tickets/{number}/take", {"version": version(app, number)})
    post(agent, f"/tickets/{number}/priority", {"priority": "high", "version": version(app, number)})
    text = page(browser(app, team["customer"]), f"/tickets/{number}")
    assert "Maria Santos sent the ticket" in text
    assert "Ana Reyes took it" in text
    assert "Ana Reyes moved it from New to Open" in text
    assert "Ana Reyes changed the priority from Normal to High" in text


# ---------- The Audit Log page ----------

@pytest.mark.parametrize("who, status", [("customer", 403), ("agent", 403), ("lead", 200), ("admin", 200)])
def test_who_opens_the_audit_log(app, team, who, status):
    assert browser(app, team[who]).get("/audit").status_code == status


@pytest.mark.parametrize("who, status", [("lead", 403), ("admin", 200)])
def test_only_admins_download_it(app, team, who, status):
    make_ticket(app, team["customer"])
    response = browser(app, team[who]).get("/audit.csv")
    assert response.status_code == status
    if status == 200:
        text = response.get_data(as_text=True)
        assert response.mimetype == "text/csv" and "Maria Santos" in text and "created" in text


def test_the_audit_log_filters(app, team):
    first = make_ticket(app, team["customer"], subject="First")
    make_ticket(app, team["other_customer"], subject="Second")
    lead = browser(app, team["lead"])
    post(lead, f"/tickets/{first}/priority", {"priority": "urgent", "version": version(app, first)})
    everything = page(lead, "/audit")
    assert "Jose Rizal" in everything and "Sent the ticket" in everything
    assert "Changed the priority from Normal to Urgent" in everything
    by_who = page(lead, "/audit?who=Jose+Rizal")
    assert "Sent the ticket" in by_who and "Changed the priority" not in by_who
    by_what = page(lead, "/audit?what=triage")
    assert "Changed the priority" in by_what and "Sent the ticket" not in by_what
    by_ticket = page(lead, "/audit?ticket=TT-2")
    assert "Sent the ticket" in by_ticket and "Changed the priority" not in by_ticket
    assert "Nothing Found" in page(lead, "/audit?ticket=TT-999")
    assert "Nothing Found" in page(lead, "/audit?since=2001-01-01&until=2001-01-02")


def test_the_audit_log_says_when_the_chain_is_broken(app, team):
    make_ticket(app, team["customer"])
    lead = browser(app, team["lead"])
    assert "History intact: 1 line checked" in page(lead, "/audit")
    with app.app_context():
        s = db.session()
        s.execute(update(AuditEvent).values(new_value="Something else"))
        s.commit()
    text = page(lead, "/audit")
    assert "The history was changed outside Ticket Tock." in text and "Line 1 was changed" in text


def test_verify_audit_command(app, team):
    make_ticket(app, team["customer"])
    runner = app.test_cli_runner()
    result = runner.invoke(args=["verify-audit"])
    assert result.exit_code == 0 and "Chain OK" in result.output
    with app.app_context():
        s = db.session()
        s.execute(update(AuditEvent).values(actor_name="Mallory"))
        s.commit()
    result = runner.invoke(args=["verify-audit"])
    assert result.exit_code != 0 and "BROKEN" in result.output


def test_admin_actions_are_in_the_history(app, team):
    admin = browser(app, team["admin"])
    post(admin, f"/admin/staff/{team['agent']}/role", {"role": "lead"})
    post(admin, f"/admin/staff/{team['other_agent']}/active", {"active": "0"})
    post(admin, "/admin/staff/invite", {"role": "agent"})
    post(admin, "/admin/categories", {"name": "Shipping"})
    text = page(admin, "/audit?what=people")
    assert "Made Ana Reyes (ana@example.com) a Team Lead (was an Agent)" in text
    assert "Deactivated the account of Carlo Diaz" in text
    assert "Made an invite link for an Agent" in text
    assert "Added the category Shipping" in page(admin, "/audit?what=settings")


def test_joining_with_an_invite_is_in_the_history(app, team):
    from .test_auth import join, make_invite
    token = make_invite(app, role="lead", admin=team["admin"])
    join(browser(app), token, email="new.lead@example.com")
    assert "Joined the team as a Team Lead" in page(browser(app, team["admin"]), "/audit")


# ---------- The Inbox ----------

def notices(app, user_id):
    with app.app_context():
        return [(n.kind, n.actor_name, n.detail) for n in
                db.session().scalars(select(Notification).where(Notification.user_id == user_id).order_by(Notification.id))]


def test_the_customer_hears_about_replies_and_status(app, team):
    number = make_ticket(app, team["customer"], status="in_progress", assignee_id=team["agent"])
    agent = browser(app, team["agent"])
    post(agent, f"/tickets/{number}/reply", {"body": "Try again now, please.", "then": "waiting"})
    assert notices(app, team["customer"]) == [("reply", "Ana Reyes", "Try again now, please."),
                                             ("status", "Ana Reyes", "waiting")]
    assert notices(app, team["agent"]) == []  # never the person who did it


def test_the_assignee_hears_about_customer_replies_and_assignments(app, team):
    number = make_ticket(app, team["customer"])
    lead = browser(app, team["lead"])
    post(lead, f"/tickets/{number}/assign", {"assignee_id": team["agent"], "version": version(app, number)})
    post(browser(app, team["customer"]), f"/tickets/{number}/reply", {"body": "Any news?"})
    assert notices(app, team["agent"]) == [("assigned", "Ben Torres", ""), ("reply", "Maria Santos", "Any news?")]


def test_internal_notes_never_notify_the_customer(app, team):
    number = make_ticket(app, team["customer"], status="open", assignee_id=team["agent"])
    post(browser(app, team["lead"]), f"/tickets/{number}/reply", {"body": "Check the logs.", "internal": "1"})
    assert notices(app, team["customer"]) == []
    assert notices(app, team["agent"]) == [("note", "Ben Torres", "")]


def test_opening_a_notice_marks_it_read(app, team):
    number = make_ticket(app, team["customer"], status="open", assignee_id=team["agent"])
    post(browser(app, team["agent"]), f"/tickets/{number}/reply", {"body": "Hello!"})
    customer = browser(app, team["customer"])
    inbox = page(customer, "/inbox")
    assert "Ana Reyes replied" in inbox and "(unread)" in inbox
    with app.app_context():
        notice_id = db.session().scalar(select(Notification.id))
    response = post(customer, f"/inbox/{notice_id}/open")
    assert response.location.endswith(f"/tickets/{number}")
    assert "(unread)" not in page(customer, "/inbox")
    assert "All Caught Up" in page(customer, "/inbox?show=unread")


def test_nobody_opens_someone_elses_notice(app, team):
    number = make_ticket(app, team["customer"], status="open", assignee_id=team["agent"])
    post(browser(app, team["agent"]), f"/tickets/{number}/reply", {"body": "Hello!"})
    with app.app_context():
        notice_id = db.session().scalar(select(Notification.id))
    assert post(browser(app, team["other_customer"]), f"/inbox/{notice_id}/open").status_code == 404


def test_mark_all_as_read(app, team):
    number = make_ticket(app, team["customer"], status="open", assignee_id=team["agent"])
    agent = browser(app, team["agent"])
    for word in ("One", "Two"):
        post(agent, f"/tickets/{number}/reply", {"body": word})
    customer = browser(app, team["customer"])
    post(customer, "/inbox/read")
    assert "All Caught Up" in page(customer, "/inbox?show=unread")


# ---------- Email (optional) ----------

def test_no_email_without_mail_settings(app, team):
    number = make_ticket(app, team["customer"], status="open", assignee_id=team["agent"])
    post(browser(app, team["agent"]), f"/tickets/{number}/reply", {"body": "Hello!"})
    assert mailer.outbox(app) == []


def test_emails_go_out_after_the_change_is_saved(app, team):
    app.config.update(MAIL_USERNAME="desk@example.com", MAIL_PASSWORD="app-password")
    number = make_ticket(app, team["customer"], status="open", assignee_id=team["agent"])
    post(browser(app, team["agent"]), f"/tickets/{number}/reply", {"body": "Fixed it for you."})
    sent = mailer.outbox(app)
    assert len(sent) == 1
    assert sent[0]["To"] == "maria@example.com" and sent[0]["Subject"] == f"[TT-{number}] New reply: Can't log in"
    assert "Fixed it for you." in sent[0].get_content()
    with app.app_context():
        assert db.session().scalar(select(Notification.email_status)) == "sent"


def test_people_can_turn_emails_off(app, team):
    app.config.update(MAIL_USERNAME="desk@example.com", MAIL_PASSWORD="app-password")
    customer = browser(app, team["customer"])
    post(customer, "/settings", {"action": "emails"})  # the box unticked
    with app.app_context():
        assert db.session().get(User, team["customer"]).email_updates is False
    number = make_ticket(app, team["customer"], status="open", assignee_id=team["agent"])
    post(browser(app, team["agent"]), f"/tickets/{number}/reply", {"body": "Hello!"})
    assert mailer.outbox(app) == []
    assert notices(app, team["customer"])  # the Inbox still has it


def test_the_audit_search_box_finds_people_tickets_and_words(app, team):
    first = make_ticket(app, team["customer"], subject="Refund for October")
    make_ticket(app, team["other_customer"], subject="Login trouble")
    lead = browser(app, team["lead"])
    assert "Sent the ticket" in page(lead, "/audit?q=refund").split("<tbody>")[1]  # the subject is on that line
    by_person = page(lead, "/audit?q=jose")
    assert "Jose Rizal" in by_person and "Maria Santos" not in by_person.split("<tbody>")[1]
    by_ticket = page(lead, f"/audit?q=TT-{first}")
    assert "Maria Santos" in by_ticket.split("<tbody>")[1] and "Jose Rizal" not in by_ticket.split("<tbody>")[1]
