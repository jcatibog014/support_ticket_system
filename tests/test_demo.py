"""Stage 8: Try Demo, View As, and "every page the demo can open shows an example"."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from tickettock import audit, db, mailer
from tickettock.dates import utcnow
from tickettock.models import Article, Desk, Ticket, User

from .conftest import CSRF, browser, is_logged_in, make_team, make_ticket, post


def start_demo(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    response = post(client, "/demo")
    assert response.status_code == 302
    return client


def be(client, role):
    assert post(client, "/demo/view-as", {"role": role}).status_code == 302
    return client


def demo_desk(app):
    with app.app_context():
        return db.session().scalar(select(Desk).where(Desk.is_demo.is_(True)).order_by(Desk.id.desc()))


def ticket_numbers(app, desk):
    with app.app_context():
        return db.session().scalars(select(Ticket.number).where(Ticket.desk_id == desk.id).order_by(Ticket.number)).all()


def test_try_demo_makes_a_full_sample_desk(app):
    client = start_demo(app)
    desk = demo_desk(app)
    assert desk.demo_expires_at > utcnow() + timedelta(hours=23)
    with app.app_context():
        s = db.session()
        statuses = set(s.scalars(select(Ticket.status).where(Ticket.desk_id == desk.id)))
        assert statuses == {"new", "open", "in_progress", "waiting", "resolved", "closed", "cancelled"}
        assert audit.verify(s, desk)[0]
    assert "Ben Torres" in client.get("/").get_data(as_text=True)


@pytest.mark.parametrize("role, name", [("customer", "Maria Santos"), ("agent", "Ana Reyes"),
                                        ("lead", "Ben Torres"), ("admin", "Sam Rivera")])
def test_view_as_plays_every_role(app, role, name):
    client = be(start_demo(app), role)
    assert name in client.get("/settings").get_data(as_text=True)


def test_view_as_is_only_for_the_demo(app):
    team = make_team(app)
    assert post(browser(app, team["admin"]), "/demo/view-as", {"role": "customer"}).status_code == 403


def test_demos_never_see_the_real_desk_or_each_other(app):
    team = make_team(app)
    real = make_ticket(app, team["customer"], subject="Real customer problem")
    first, second = start_demo(app), start_demo(app)
    be(first, "admin")
    assert "Real customer problem" not in first.get("/queue?tab=open").get_data(as_text=True)
    with app.app_context():
        desks = db.session().scalars(select(Desk.id).where(Desk.is_demo.is_(True))).all()
    assert len(desks) == 2
    # The real desk's people never see demo tickets.
    assert "Kape Cloud" not in browser(app, team["lead"]).get("/queue?tab=open").get_data(as_text=True)
    assert real == 1


def test_demo_people_cannot_log_in_with_the_form(app):
    start_demo(app)
    response = post(app.test_client(), "/login", {"email": "ben@kapecloud.example", "password": "anything"})
    assert response.status_code == 401


def test_an_expired_demo_logs_out_and_is_deleted(app):
    client = start_demo(app)
    with app.app_context():
        s = db.session()
        desk = s.scalar(select(Desk).where(Desk.is_demo.is_(True)))
        desk.demo_expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert not is_logged_in(client)
    start_demo(app)  # starting a new one deletes the old one, with everything in it
    with app.app_context():
        s = db.session()
        assert s.scalar(select(func.count()).select_from(Desk).where(Desk.is_demo.is_(True))) == 1
        assert s.scalar(select(func.count()).select_from(User).where(User.desk_id == desk.id)) == 0


def test_the_demo_cannot_change_accounts_or_send_email(app):
    app.config.update(MAIL_USERNAME="desk@example.com", MAIL_PASSWORD="x")
    client = be(start_demo(app), "admin")
    assert post(client, "/settings", {"action": "name", "name": "Hacker"}).status_code == 403
    assert post(client, "/admin/staff/invite", {"role": "admin"}).status_code == 403
    be(client, "agent")
    desk = demo_desk(app)
    number = ticket_numbers(app, desk)[0]
    post(client, f"/tickets/{number}/reply", {"body": "Hello from the demo"})
    assert mailer.outbox(app) == []


def test_the_demo_note_is_a_toast_once_then_a_small_tag(app):
    client = start_demo(app)
    first = client.get("/").get_data(as_text=True)
    assert 'class="toast toast-demo"' in first and 'data-toast-wait="15000"' in first
    assert "deleted after 24 hours" in first and 'href="/signup"' in first
    later = client.get("/queue").get_data(as_text=True)
    assert "toast-demo" not in later and "demo-bar" not in later  # no line taking space on every page
    assert '<span class="demo-tag">Demo</span>' in later and 'class="demo-signup"' in later
    team = make_team(app)
    real = browser(app, team["agent"]).get("/queue").get_data(as_text=True)
    assert "demo-tag" not in real and "demo-signup" not in real


def test_sign_up_from_the_demo_menu_leaves_the_demo(app):
    client = start_demo(app)
    assert "Sign Up" in client.get("/signup").get_data(as_text=True)
    assert not is_logged_in(client)


# ---------- Every page shows an example ----------

PAGES = {
    "customer": ["/tickets", "/tickets/new", "/inbox", "/help", "/settings"],
    "agent": ["/", "/queue?tab=unassigned", "/queue?tab=mine", "/queue?tab=overdue", "/queue?tab=open",
              "/queue?tab=waiting", "/queue?tab=resolved", "/queue?tab=closed", "/inbox", "/saved-replies", "/help"],
    "lead": ["/", "/queue?tab=mine", "/inbox", "/insights?days=7", "/insights?days=30", "/insights?days=90", "/audit",
             "/audit?what=escalations", "/audit?what=ratings", "/audit?what=talk", "/saved-replies"],
    "admin": ["/admin/staff", "/admin/categories", "/admin/hours", "/inbox", "/audit?what=people"],
}


@pytest.mark.parametrize("role", list(PAGES))
def test_every_demo_page_shows_an_example(app, role):
    client = be(start_demo(app), role)
    for url in PAGES[role]:
        response = client.get(url)
        text = response.get_data(as_text=True)
        assert response.status_code == 200, url
        assert 'class="empty-state' not in text, f"{url} is empty in the demo"
        assert "None yet" not in text, f"{url} has a number missing in the demo"


@pytest.mark.parametrize("role", ["customer", "agent"])
def test_every_ticket_page_opens(app, role):
    client = be(start_demo(app), role)
    desk = demo_desk(app)
    opened = 0
    for number in ticket_numbers(app, desk):
        status = client.get(f"/tickets/{number}").status_code
        assert status in (200, 404)  # a customer opens only their own
        opened += status == 200
    assert opened >= 3


def test_the_demo_has_articles_pictures_ratings_and_escalations(app):
    start_demo(app)
    desk = demo_desk(app)
    with app.app_context():
        s = db.session()
        from tickettock.models import Attachment, SavedReply
        assert s.scalar(select(func.count()).select_from(Article).where(Article.desk_id == desk.id)) >= 6
        assert s.scalar(select(func.count()).select_from(SavedReply).where(SavedReply.desk_id == desk.id)) >= 3
        assert s.scalar(select(func.count()).select_from(Attachment)) >= 2
        rows = s.scalars(select(Ticket).where(Ticket.desk_id == desk.id)).all()
        assert sum(1 for t in rows if t.rating) >= 3
        assert any(t.escalated_at for t in rows) and any(t.merged_into_id for t in rows)
        assert any(t.priority == "urgent" for t in rows)


def test_the_demo_has_someone_also_looking(app):
    client = be(start_demo(app), "agent")
    desk = demo_desk(app)
    with app.app_context():
        number = db.session().scalar(select(Ticket.number).where(Ticket.desk_id == desk.id,
                                                                 Ticket.subject == "Charged twice for November"))
    assert "Carlo Diaz is also looking at this ticket." in client.get(f"/tickets/{number}").get_data(as_text=True)


def test_purge_demos_command(app):
    start_demo(app)
    with app.app_context():
        s = db.session()
        desk = s.scalar(select(Desk).where(Desk.is_demo.is_(True)))
        desk.demo_expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    result = app.test_cli_runner().invoke(args=["purge-demos"])
    assert "Deleted 1 old demo desks." in result.output


@pytest.mark.parametrize("hour", [1, 7, 12, 18, 23])
def test_the_demo_is_complete_at_any_time_of_day(app, hour):
    """Today's sample tickets count back from now, so a demo started at 1 AM has them too."""
    from datetime import datetime, time

    from tickettock import seed
    from tickettock.dates import from_local, today
    moment = from_local(datetime.combine(today(), time(hour, 5)))
    with app.app_context():
        s = db.session()
        desk, _ = seed.build(s, moment)
        s.commit()
        subjects = set(s.scalars(select(Ticket.subject).where(Ticket.desk_id == desk.id)))
        assert len(subjects) == len(seed.STORIES)
        assert all(created <= moment for created in s.scalars(select(Ticket.created_at).where(Ticket.desk_id == desk.id)))
