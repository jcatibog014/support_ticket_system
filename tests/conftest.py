import os

import pytest
from sqlalchemy import select

from tickettock import auth, create_app, db, desks
from tickettock.models import ADMIN, AGENT, CUSTOMER, LEAD, User

# Tests use a fresh SQLite file per test. To check everything on PostgreSQL
# too, set TEST_DATABASE_URL to an EMPTY Neon branch (its tables are dropped
# and made again for every test, so never point it at the live database).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

CSRF = "test-csrf-token"
PASSWORD = "correct horse"


@pytest.fixture(autouse=True)
def fast_passwords(monkeypatch):
    """The real password hash is slow on purpose (to slow down guessing). Tests make
    hundreds of users, so they use a quick setting of the same kind of hash."""
    from functools import partial

    from werkzeug.security import generate_password_hash
    monkeypatch.setattr(auth, "generate_password_hash", partial(generate_password_hash, method="pbkdf2:sha256:1000"))


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DATABASE": str(tmp_path / "test.sqlite"),
        "DATABASE_URL": TEST_DATABASE_URL,
        "SERVER_NAME": "localhost",
    })
    with app.app_context():
        if TEST_DATABASE_URL:
            db.drop_tables()
        db.create_tables()
        s = db.session()
        desks.create_desk(s)
        s.commit()
    return app


@pytest.fixture
def ctx(app):
    """An app context, for tests that use db.session() directly."""
    with app.app_context():
        yield db.session()


@pytest.fixture
def client(app):
    client = app.test_client()
    # Every POST needs the session's CSRF token. Put a known one in the session.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client


def post(client, url, data=None, **kwargs):
    """POST a form with the right CSRF token, like a real page would."""
    # Logging in or out starts a new session (and a new token), so set it every time.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client.post(url, data={"csrf_token": CSRF, **(data or {})}, **kwargs)


def main_desk_id(app):
    with app.app_context():
        return desks.main_desk(db.session()).id


def make_user(app, name="Maria Santos", email=None, role=CUSTOMER, desk_id=None, **fields):
    """Put a user straight into the database. Returns the id."""
    with app.app_context():
        s = db.session()
        desk = s.get(desks.Desk, desk_id) if desk_id else desks.main_desk(s)
        email = email or f"{name.split()[0].lower()}@example.com"
        user = auth.create_user(s, desk, name, email, PASSWORD, role=role)
        for key, value in fields.items():
            setattr(user, key, value)
        s.commit()
        return user.id


def make_team(app):
    """One of each role on the main desk: {"customer": id, "agent": id, ...}."""
    return {
        "customer": make_user(app, "Maria Santos", role=CUSTOMER),
        "other_customer": make_user(app, "Jose Rizal", role=CUSTOMER),
        "agent": make_user(app, "Ana Reyes", role=AGENT),
        "other_agent": make_user(app, "Carlo Diaz", role=AGENT),
        "lead": make_user(app, "Ben Torres", role=LEAD),
        "admin": make_user(app, "Sam Rivera", role=ADMIN),
    }


def log_in_as(client, user_id):
    """Skip the login form: put the user straight into the session."""
    with client.application.app_context():
        version = db.session().get(User, user_id).session_version
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["session_version"] = version
        session["csrf"] = CSRF
    return client


def browser(app, user_id=None):
    """A fresh client, logged in as this user if given."""
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return log_in_as(client, user_id) if user_id else client


def is_logged_in(client):
    """The real check: load_user() runs on every request and drops a session that
    is no longer valid, so ask after a request what is left in the cookie."""
    client.get("/settings")
    with client.session_transaction() as session:
        return "user_id" in session


def get_user(app, email):
    with app.app_context():
        return db.session().scalar(select(User).where(User.email == email))


def make_ticket(app, customer_id, subject="Can't log in", status="new", assignee_id=None, **fields):
    """A ticket made the real way (tickets.create, so it has a history line), then
    put straight into a status. Returns its number."""
    from tickettock import tickets
    from tickettock.dates import utcnow
    with app.app_context():
        s = db.session()
        customer = s.get(User, customer_id)
        ticket = tickets.create(s, customer, subject, "It says my password is wrong.", fields.pop("now", utcnow()))
        ticket.status = status
        ticket.assignee_id = assignee_id
        for key, value in fields.items():
            setattr(ticket, key, value)
        s.commit()
        return ticket.number


def get_ticket(app, number, desk_id=None):
    from tickettock.models import Ticket
    with app.app_context():
        s = db.session()
        desk_id = desk_id or desks.main_desk(s).id
        ticket = s.scalar(select(Ticket).where(Ticket.desk_id == desk_id, Ticket.number == number))
        ticket.messages  # load them while the session is open
        return ticket
