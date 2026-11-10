"""Logging in and out, signing up, joining as staff, recovery codes, and protection for every form.

Customers sign up themselves (with their name, email and a password). Staff
join with a one-time invite link from the Admin (invites.py), which sets their
role. The first Admin is made on the command line: flask --app tickettock create-admin.

Passwords are stored only as hashes. The login cookie holds the user's id
and their session_version; load_user() checks both on every request, so an
old cookie (after a password change) is logged out straight away.

No email is needed: at sign-up everyone gets a recovery code, shown once,
that can set a new password on the Forgot Password page.
"""
import functools
import hmac
import re
import secrets
from datetime import timedelta

import click
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from flask.cli import with_appcontext
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from . import audit, db, desks, invites, mailer, permissions
from .dates import utcnow
from .models import A_ROLE, ADMIN, CUSTOMER, LEAD, Desk, User

bp = Blueprint("auth", __name__)

NAME_MAX_LENGTH = 80
EMAIL_MAX_LENGTH = 254
PASSWORD_MIN_LENGTH = 8
# Good enough to catch typos. Nothing is ever sent to it without the person asking.
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# After 5 wrong passwords (or recovery codes) in a row, the account waits 15 minutes.
MAX_FAILED_LOGINS = 5
LOCK_TIME = timedelta(minutes=15)
# No 0/O or 1/I/L, so a code copied by hand is read back the same.
RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

# Every Try Demo makes its own sample desk (seed.py), deleted after 24 hours.
# This many at once is plenty, and keeps a script pressing Try Demo from filling the free database.
MAX_DEMOS = 300

WRONG_LOGIN = "Wrong email or password."
WRONG_RECOVERY = "Wrong email or recovery code."
LOCKED = "Too many failed attempts. Please wait 15 minutes and try again."


# ---------- Checking what people type ----------

def clean_email(text):
    return (text or "").strip().lower()[:EMAIL_MAX_LENGTH + 1]


def email_problem(email):
    if not email:
        return "Enter your email."
    if len(email) > EMAIL_MAX_LENGTH or not EMAIL_PATTERN.fullmatch(email):
        return "That doesn't look like an email address."
    return None


def name_problem(name):
    if not name:
        return "Enter your name."
    if len(name) > NAME_MAX_LENGTH:
        return f"Use at most {NAME_MAX_LENGTH} characters."
    return None


def password_problem(password, confirm):
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Use at least {PASSWORD_MIN_LENGTH} characters."
    if password != confirm:
        return "The two passwords are not the same."
    return None


def account_form_problems(form):
    """(name, email, password, errors) from a sign-up or join form."""
    name = form.get("name", "").strip()
    email = clean_email(form.get("email", ""))
    password = form.get("password", "")
    errors = {}
    for key, problem in (("name", name_problem(name)), ("email", email_problem(email)),
                         ("password", password_problem(password, form.get("confirm_password", "")))):
        if problem:
            errors[key] = problem
    return name, email, password, errors


# ---------- Users in the database ----------

def find_user(s, desk, email):
    if desk is None:
        return None
    return s.scalar(select(User).where(User.desk_id == desk.id, User.email == clean_email(email)))


def create_user(s, desk, name, email, password, role=CUSTOMER):
    """Add a user to the desk. The caller commits (and catches IntegrityError for a used email)."""
    user = User(desk_id=desk.id, name=name, email=clean_email(email), role=role,
                password_hash=generate_password_hash(password))
    user.desk = desk
    s.add(user)
    s.flush()
    return user


def set_password(user, password):
    user.password_hash = generate_password_hash(password)
    user.failed_logins = 0
    user.locked_until = None
    user.session_version += 1  # every other device is logged out


def new_recovery_code(user):
    """Give the user a fresh code and return it. This is the only time it
    exists in the clear: the old code stops working."""
    code = "-".join("".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(4)) for _ in range(4))
    user.recovery_hash = generate_password_hash(code)
    return code


def clean_recovery_code(text):
    """People type codes in any case and with or without dashes."""
    letters = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
    return "-".join(letters[i:i + 4] for i in range(0, len(letters), 4))


def _failed(s, user, moment):
    user.failed_logins += 1
    if user.failed_logins >= MAX_FAILED_LOGINS:
        user.failed_logins, user.locked_until = 0, moment + LOCK_TIME
    s.commit()


def check_login(s, desk, email, password, moment):
    """Return (user, None) if the password is right, else (None, message).
    The message never says whether the email has an account."""
    user = find_user(s, desk, email)
    if user is None or not user.is_active:
        return None, WRONG_LOGIN
    if user.locked_until and user.locked_until > moment:
        return None, LOCKED
    if not check_password_hash(user.password_hash, password or ""):
        _failed(s, user, moment)
        return None, WRONG_LOGIN
    user.failed_logins, user.locked_until = 0, None
    s.commit()
    return user, None


def check_recovery(s, desk, email, code, moment):
    """Like check_login, but with the recovery code. Wrong codes count towards
    the same lock, so the code can't be guessed either."""
    user = find_user(s, desk, email)
    if user is None or not user.is_active or user.recovery_hash is None:
        return None, WRONG_RECOVERY
    if user.locked_until and user.locked_until > moment:
        return None, LOCKED
    if not check_password_hash(user.recovery_hash, clean_recovery_code(code)):
        _failed(s, user, moment)
        return None, WRONG_RECOVERY
    return user, None


def home_for(user):
    """Where someone lands after logging in: staff at the queue, customers at their tickets."""
    if user is not None and permissions.is_staff(user):
        return url_for("main.home")
    return url_for("main.home")


def safe_next(target, default=None):
    """Only go back to a page on this site (after logging in or saving a form).

    "/tickets/7" is fine. "https://evil.example" and "//evil.example" are not:
    a fake link could send people there after a real login. Browsers drop tabs
    and newlines and read a backslash as a slash, so those are out too.
    """
    if (target and target.startswith("/") and not target.startswith("//")
            and "\\" not in target and not any(ord(c) < 32 for c in target)):
        return target
    return default or url_for("main.home")


def log_in(user):
    csrf = session.get("csrf")
    session.clear()  # a new session for the new user (old cookies can't be reused)
    if csrf and user.is_demo:
        session["csrf"] = csrf  # View As swaps people inside one demo; its open forms keep working
    session.permanent = True  # stay logged in for PERMANENT_SESSION_LIFETIME
    session["user_id"] = user.id
    session["session_version"] = user.session_version


def show_recovery_code(code):
    """The next page shows the code once (recovery_code() below)."""
    session["recovery_code"] = code


# ---------- Every request ----------

@bp.before_app_request
def load_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        user = db.session().get(User, user_id)
        if (user is None or session.get("session_version") != user.session_version or not user.is_active
                or (user.desk.is_demo and user.desk.demo_expires_at and user.desk.demo_expires_at <= utcnow())):
            # Keep the CSRF token: only the login part of this cookie is out of date.
            session.pop("user_id", None)
            session.pop("session_version", None)
        else:
            g.user = user
    # Which desk this page is about: your own (a demo has its own), else the real one.
    g.desk = g.user.desk if g.user else desks.main_desk(db.session())


def csrf_token():
    """A secret per session, put into every form as a hidden field.

    Another website could make your browser send a form to this site (your
    login cookie goes with it). It can't read this secret, so its form fails.
    """
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@bp.before_app_request
def check_csrf():
    if request.method != "POST" or request.endpoint in current_app.config.get("CSRF_EXEMPT", ()):
        return
    sent = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    # compare_digest takes the same time for every wrong guess.
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, "This form has expired. Please go back, reload the page and try again.")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            # Only a GET can be repeated after logging in.
            next_page = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("auth.login", next=next_page))
        return view(*args, **kwargs)
    return wrapped


def require(allowed, message="You can't do that."):
    """403 unless a rule from permissions.py said yes."""
    if not allowed:
        abort(403, message)


def not_for_demo(message="The demo can't change this. Sign up to make it your own."):
    if g.user is not None and g.user.is_demo:
        abort(403, message)


def need_desk():
    """Sign-up and login need the real desk to exist (flask create-admin makes it)."""
    if g.desk is None:
        abort(503, "Ticket Tock isn't set up yet. The owner runs: flask --app tickettock create-admin")


# ---------- Pages ----------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(home_for(g.user))
    next_page = request.values.get("next", "")
    error = None
    email = ""
    if request.method == "POST":
        need_desk()
        email = clean_email(request.form.get("email", ""))
        user, error = check_login(db.session(), g.desk, email, request.form.get("password", ""), utcnow())
        if user:
            log_in(user)
            return redirect(safe_next(next_page, home_for(user)))
    status = 401 if error else 200
    return render_template("login.html", error=error, email=email, next=next_page), status


def purge_demos(s, moment):
    """Delete every demo desk that has run out. The database deletes everything in it (ON DELETE CASCADE)."""
    gone = s.execute(delete(Desk).where(Desk.is_demo.is_(True), Desk.demo_expires_at <= moment)
                     .execution_options(synchronize_session=False))
    return gone.rowcount


@bp.post("/demo")
def demo():
    """A fresh sample desk for this visitor alone. Nobody sees anyone else's demo,
    and the real desk is never touched."""
    from . import seed
    s = db.session()
    moment = utcnow()
    if g.user is not None and g.user.is_demo:
        return redirect(url_for("main.home"))  # this browser already has one
    purge_demos(s, moment)
    s.commit()
    if s.scalar(select(func.count()).select_from(Desk).where(Desk.is_demo.is_(True))) >= MAX_DEMOS:
        flash("The demo is very busy right now. Please try again in a little while.", "error")
        return redirect(url_for("auth.login"))
    desk, people = seed.build(s, moment)
    s.commit()
    log_in(seed.view_as_user(s, desk, LEAD))
    flash("Welcome to the demo! You are Ben Torres, a Team Lead at Kape Cloud. "
          "Use View As to be a customer, an agent or the admin.")
    return redirect(url_for("main.home"))


@bp.post("/demo/view-as")
@login_required
def view_as():
    """Play another role in your own demo desk: log in as that sample person."""
    from . import seed
    if not g.user.is_demo:
        abort(403, "View As is only for the demo.")
    role = request.form.get("role", "")
    if role not in seed.VIEW_AS:
        abort(400, "Choose a role from the list.")
    person = seed.view_as_user(db.session(), g.desk, role)
    if person is None or not person.is_active:
        abort(409, "That sample person isn't here any more (switched off in Staff).")
    log_in(person)  # keeps the CSRF token: it is the same visitor
    flash(f"You are now {person.name}, {A_ROLE[person.role]}.")
    return redirect(home_for(person))


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Customers only. Staff join with an invite link (join below)."""
    if g.user is not None and g.user.is_demo:
        # "Sign up to keep your work" in the demo bar: leave the demo first.
        session.pop("user_id", None)
        session.pop("session_version", None)
        g.user, g.desk = None, desks.main_desk(db.session())
    if g.user is not None:
        return redirect(home_for(g.user))
    next_page = request.values.get("next", "")
    errors = {}
    name = email = ""
    if request.method == "POST":
        need_desk()
        # A hidden field people never see. Only robots fill it in, and they get
        # the same page as everyone else, so they learn nothing from it.
        if request.form.get("website"):
            return redirect(url_for("auth.login"))
        name, email, password, errors = account_form_problems(request.form)
        if not errors:
            s = db.session()
            try:
                user = create_user(s, g.desk, name, email, password)
                code = new_recovery_code(user)
                s.commit()
            except IntegrityError:
                s.rollback()
                errors["email"] = "That email already has an account. Log in instead."
            else:
                log_in(user)
                show_recovery_code(code)
                return redirect(url_for("auth.recovery_code", next=safe_next(next_page)))
    return render_template("signup.html", errors=errors, name=name, email=email,
                           next=next_page), 400 if errors else 200


@bp.route("/join/<token>", methods=["GET", "POST"])
def join(token):
    """An invite link: make a staff account with the role the link gives."""
    s = db.session()
    now = utcnow()
    invite = invites.find_usable(s, token, now)
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}  # the address holds a secret
    if invite is None:
        return render_template("join.html", invite=None, errors={}, name="", email=""), 404, headers
    if g.user is not None:
        return render_template("join.html", invite=invite, logged_in=True, errors={}, name="", email=""), 200, headers
    errors = {}
    name = email = ""
    if request.method == "POST":
        name, email, password, errors = account_form_problems(request.form)
        if not errors:
            desk = s.get(Desk, invite.desk_id)
            try:
                user = create_user(s, desk, name, email, password, role=invite.role)
                if not invites.claim(s, invite, user, now):
                    s.rollback()
                    return render_template("join.html", invite=None, errors={}, name="", email=""), 404, headers
                audit.log(s, desk.id, user, audit.JOINED, now, field="role", new=user.role, note=user.email)
                code = new_recovery_code(user)
                s.commit()
            except IntegrityError:
                s.rollback()
                errors["email"] = "That email already has an account here. Ask the Admin to change its role."
            else:
                log_in(user)
                show_recovery_code(code)
                flash(f"Welcome to the team! You joined as {user.role_name}.")
                return redirect(url_for("auth.recovery_code"))
    return render_template("join.html", invite=invite, errors=errors, name=name, email=email), \
        400 if errors else 200, headers


@bp.get("/recovery-code")
@login_required
def recovery_code():
    """Shown once, straight after the code is made. A reload shows nothing."""
    code = session.pop("recovery_code", None)
    if code is None:
        return redirect(url_for("auth.settings"))
    return render_template("recovery_code.html", code=code, next=safe_next(request.args.get("next")))


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if g.user is not None:
        return redirect(url_for("auth.settings"))
    errors = {}
    email = ""
    if request.method == "POST":
        need_desk()
        s = db.session()
        email = clean_email(request.form.get("email", ""))
        new = request.form.get("new_password", "")
        problem = password_problem(new, request.form.get("confirm_password", ""))
        if problem:
            errors["new_password"] = problem
        else:
            user, error = check_recovery(s, g.desk, email, request.form.get("code", ""), utcnow())
            if error:
                errors["code"] = error
            else:
                set_password(user, new)
                code = new_recovery_code(user)  # each code works only once
                s.commit()
                log_in(user)
                show_recovery_code(code)
                flash("Your password has been changed.")
                return redirect(url_for("auth.recovery_code"))
    return render_template("forgot.html", errors=errors, email=email), 400 if errors else 200


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have logged out.")
    return redirect(url_for("auth.login"))


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    errors = {}
    if request.method == "POST":
        not_for_demo("The demo can't change its account. Sign up to have your own.")
        action = request.form.get("action")
        s = db.session()
        if action == "name":
            name = request.form.get("name", "").strip()
            problem = name_problem(name)
            if problem:
                errors["name"] = problem
            else:
                g.user.name = name
                s.commit()
                flash("Your name has been changed.")
                return redirect(url_for("auth.settings"))
        elif action == "emails":
            g.user.email_updates = request.form.get("email_updates") == "1"
            s.commit()
            flash("We'll email you about your tickets." if g.user.email_updates
                  else "No more emails. Everything still comes to your Inbox.")
            return redirect(url_for("auth.settings"))
        elif action in ("password", "recovery"):
            if not check_password_hash(g.user.password_hash, request.form.get("current_password", "")):
                errors[f"{action}_current"] = "That isn't your current password."
            if action == "password":
                new = request.form.get("new_password", "")
                problem = password_problem(new, request.form.get("confirm_password", ""))
                if problem:
                    errors["new_password"] = problem
            if not errors:
                if action == "password":
                    set_password(g.user, new)
                    s.commit()
                    # This device stays logged in; every other one is logged out.
                    session["session_version"] = g.user.session_version
                    flash("Your password has been changed.")
                    return redirect(url_for("auth.settings"))
                show_recovery_code(new_recovery_code(g.user))
                s.commit()
                return redirect(url_for("auth.recovery_code"))
    return render_template("settings.html", errors=errors, mail_on=mailer.enabled()), 400 if errors else 200


# ---------- Command line ----------

def ask_password():
    password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    if len(password) < PASSWORD_MIN_LENGTH:
        raise click.ClickException(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    return password


@click.command("create-admin")
@with_appcontext
@click.option("--name", prompt="Your name")
@click.option("--email", prompt="Your email")
def create_admin_command(name, email):
    """Make the desk (the first time) and an Admin's login."""
    s = db.session()
    email = clean_email(email)
    problem = name_problem(name.strip()) or email_problem(email)
    if problem:
        raise click.ClickException(problem)
    desk = desks.main_desk(s) or desks.create_desk(s)
    if find_user(s, desk, email):
        raise click.ClickException(f"{email} already has an account. Use set-password to change the password.")
    user = create_user(s, desk, name.strip(), email, ask_password(), role=ADMIN)
    code = new_recovery_code(user)
    s.commit()
    click.echo(f"Made the Admin {email} for {desk.name}. Log in at /login.")
    click.echo(f"Your recovery code (keep it safe, it is shown only now): {code}")


@click.command("set-password")
@with_appcontext
@click.argument("email")
def set_password_command(email):
    """Change someone's password (and unlock the account). Also ends their sessions."""
    s = db.session()
    user = find_user(s, desks.main_desk(s), email)
    if user is None:
        raise click.ClickException(f"There is no account for {email}.")
    set_password(user, ask_password())
    s.commit()
    click.echo("The password is changed.")


@click.command("purge-demos")
@with_appcontext
def purge_demos_command():
    """Delete the demos that have run out (starting a demo does this too)."""
    s = db.session()
    count = purge_demos(s, utcnow())
    s.commit()
    click.echo(f"Deleted {count} old demo desks.")


def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["can"] = permissions
    for command in (create_admin_command, set_password_command, purge_demos_command):
        app.cli.add_command(command)
