"""Signing up, logging in, joining with an invite, recovery codes and form protection."""
import re

from tickettock import db, invites
from tickettock.dates import utcnow
from tickettock.models import AGENT, Desk, User

from .conftest import PASSWORD, browser, get_user, is_logged_in, log_in_as, main_desk_id, make_user, post


def sign_up(client, name="Maria Santos", email="maria@example.com", password=PASSWORD, confirm=None, **extra):
    return post(client, "/signup", {"name": name, "email": email, "password": password,
                                    "confirm_password": confirm if confirm is not None else password, **extra})


def test_sign_up_makes_a_customer_and_shows_the_code_once(client, app):
    response = sign_up(client, email="  Maria@Example.com ")
    assert response.status_code == 302 and "/recovery-code" in response.location
    user = get_user(app, "maria@example.com")
    assert user.role == "customer" and user.recovery_hash
    page = client.get("/recovery-code")
    assert re.search(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", page.get_data(as_text=True))
    assert client.get("/recovery-code").status_code == 302  # gone after one look


def test_sign_up_checks_what_people_type(client):
    page = sign_up(client, name="", email="not-an-email", password="short")
    text = page.get_data(as_text=True)
    assert page.status_code == 400
    assert "Enter your name." in text and "doesn&#39;t look like an email" in text and "at least 8" in text
    assert sign_up(client, confirm="different!").status_code == 400


def test_one_account_per_email(client, app):
    make_user(app, email="maria@example.com")
    page = sign_up(client, email="MARIA@example.com")
    assert page.status_code == 400 and "already has an account" in page.get_data(as_text=True)


def test_robots_filling_the_hidden_field_get_nothing(client, app):
    sign_up(client, website="spam.example")
    assert get_user(app, "maria@example.com") is None


def test_log_in_and_out(client, app):
    make_user(app, email="maria@example.com")
    bad = post(client, "/login", {"email": "maria@example.com", "password": "wrong password"})
    assert bad.status_code == 401 and "Wrong email or password." in bad.get_data(as_text=True)
    good = post(client, "/login", {"email": "MARIA@example.com", "password": PASSWORD})
    assert good.status_code == 302
    assert is_logged_in(client)
    post(client, "/logout")
    assert not is_logged_in(client)


def test_unknown_email_gets_the_same_message(client):
    page = post(client, "/login", {"email": "nobody@example.com", "password": PASSWORD})
    assert page.status_code == 401 and "Wrong email or password." in page.get_data(as_text=True)


def test_five_wrong_passwords_lock_the_account(client, app):
    make_user(app, email="maria@example.com")
    for _ in range(5):
        post(client, "/login", {"email": "maria@example.com", "password": "nope nope"})
    page = post(client, "/login", {"email": "maria@example.com", "password": PASSWORD})
    assert "Too many failed attempts" in page.get_data(as_text=True)


def test_login_only_goes_back_to_this_site(client, app):
    make_user(app, email="maria@example.com")
    response = post(client, "/login", {"email": "maria@example.com", "password": PASSWORD,
                                       "next": "//evil.example/steal"})
    assert response.location.endswith("/")


def test_switched_off_staff_are_logged_out_and_cannot_log_in(client, app):
    agent = make_user(app, "Ana Reyes", role=AGENT)
    log_in_as(client, agent)
    assert is_logged_in(client)
    with app.app_context():
        user = db.session().get(User, agent)
        user.is_active = False
        db.session().commit()
    assert not is_logged_in(client)
    page = post(client, "/login", {"email": "ana@example.com", "password": PASSWORD})
    assert page.status_code == 401


def test_every_post_needs_the_csrf_token(client):
    response = client.post("/login", data={"email": "a@example.com", "password": "x"})
    assert response.status_code == 400


def test_forgot_password_with_the_recovery_code(client, app):
    sign_up(client)
    code = re.search(r"[A-Z2-9]{4}(-[A-Z2-9]{4}){3}", client.get("/recovery-code").get_data(as_text=True)).group()
    post(client, "/logout")
    wrong = post(client, "/forgot", {"email": "maria@example.com", "code": "AAAA-BBBB-CCCC-DDDD",
                                     "new_password": "new password", "confirm_password": "new password"})
    assert wrong.status_code == 400
    good = post(client, "/forgot", {"email": "maria@example.com", "code": code.lower().replace("-", " "),
                                    "new_password": "new password", "confirm_password": "new password"})
    assert good.status_code == 302
    post(client, "/logout")
    again = post(client, "/forgot", {"email": "maria@example.com", "code": code,
                                     "new_password": "third password", "confirm_password": "third password"})
    assert again.status_code == 400  # each code works once
    assert post(client, "/login", {"email": "maria@example.com", "password": "new password"}).status_code == 302


def test_password_change_logs_out_other_devices(app):
    user = make_user(app, email="maria@example.com")
    phone, laptop = browser(app, user), browser(app, user)
    response = post(laptop, "/settings", {"action": "password", "current_password": PASSWORD,
                                          "new_password": "brand new pw", "confirm_password": "brand new pw"})
    assert response.status_code == 302
    assert is_logged_in(laptop)
    assert not is_logged_in(phone)


def test_settings_changes_the_name(app):
    client = browser(app, make_user(app))
    post(client, "/settings", {"action": "name", "name": "Maria S."})
    assert "Maria S." in client.get("/settings").get_data(as_text=True)


# ---------- Joining with an invite link ----------

def make_invite(app, role=AGENT, admin=None):
    admin = admin or make_user(app, "Sam Rivera", role="admin")
    with app.app_context():
        s = db.session()
        token = invites.create(s, s.get(Desk, main_desk_id(app)), role, s.get(User, admin), utcnow())
        s.commit()
    return token


def join(client, token, email="carlo@example.com"):
    return post(client, f"/join/{token}", {"name": "Carlo Diaz", "email": email, "password": PASSWORD,
                                           "confirm_password": PASSWORD})


def test_an_invite_makes_staff_with_its_role_once(client, app):
    token = make_invite(app, role="lead")
    assert client.get(f"/join/{token}").status_code == 200
    response = join(client, token)
    assert response.status_code == 302
    assert get_user(app, "carlo@example.com").role == "lead"
    other = browser(app)
    assert other.get(f"/join/{token}").status_code == 404  # used up
    assert join(other, token, email="someone@example.com").status_code == 404


def test_an_unknown_invite_is_not_found(client):
    assert client.get("/join/made-up-token").status_code == 404


def test_invite_pages_are_never_cached(client, app):
    token = make_invite(app)
    response = client.get(f"/join/{token}")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_security_headers(client):
    response = client.get("/login")
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"


def test_create_admin_command(tmp_path):
    from tickettock import create_app
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "cli.sqlite"), "DATABASE_URL": None})
    runner = app.test_cli_runner()
    assert runner.invoke(args=["upgrade-db"]).exit_code == 0
    result = runner.invoke(args=["create-admin", "--name", "Sam Rivera", "--email", "sam@example.com"],
                           input=f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0, result.output
    assert "recovery code" in result.output
    with app.app_context():
        from sqlalchemy import select
        user = db.session().scalar(select(User).where(User.email == "sam@example.com"))
        assert user.role == "admin" and not user.desk.is_demo


# ---------- The Settings page (after a design review) ----------

def test_settings_has_a_switch_for_emails_and_no_view_as(app):
    client = browser(app, make_user(app, email="maria@example.com"))
    text = client.get("/settings").get_data(as_text=True)
    assert "Email Notifications" in text and 'class="switch"' in text
    assert "View As" not in text  # it lives at the bottom of the menu, for the demo only
    post(client, "/settings", {"action": "emails"})  # the switch turned off
    assert get_user(app, "maria@example.com").email_updates is False


def test_a_wrong_current_password_keeps_the_form_open(app):
    client = browser(app, make_user(app, email="maria@example.com"))
    response = post(client, "/settings", {"action": "password", "current_password": "wrong one",
                                          "new_password": "brand new pw", "confirm_password": "brand new pw"})
    text = response.get_data(as_text=True)
    assert response.status_code == 400
    assert '<details class="setting-fold" open>' in text and "That isn&#39;t your current password." in text
