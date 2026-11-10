"""Ticket Tock, a support ticket system: customers send issues, and the support
team assigns, prioritizes and resolves them. "Every ticket, on time."

Flask builds the app inside create_app() (the "app factory" pattern).
Tests call it with their own settings, so each test gets a fresh app and database.
"""
import mimetypes
import os
from datetime import timedelta
from pathlib import Path

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

# Python doesn't know this one, and browsers want the right type for the app manifest
# (the name and icons used when someone adds Ticket Tock to a home screen).
mimetypes.add_type("application/manifest+json", ".webmanifest")

APP_NAME = "Ticket Tock"
DEV_SECRET_KEY = "dev-only-not-secret"

# Content-Security-Policy: the browser may only load scripts, styles, fonts
# and images from this site. Even if someone sneaked a <script> past the
# cleaning in render.py, the browser would refuse to run it.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "font-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    on_render = bool(os.environ.get("RENDER"))  # Render sets RENDER=true on its servers
    app.config.from_mapping(
        # Signs the login cookie. On Render it comes from an environment variable.
        SECRET_KEY=os.environ.get("SECRET_KEY", DEV_SECRET_KEY),
        # Local SQLite file, in the instance/ folder (git ignores it).
        DATABASE=str(Path(app.instance_path) / "tickettock.sqlite"),
        # Set on the live site: then we use PostgreSQL (Neon) instead of SQLite.
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        # Cookie safety: not readable by JavaScript (Flask's default), not sent
        # with forms from other sites, and only over HTTPS on the live site.
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=on_render,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=1024 * 1024,  # forms only for now
        # Optional Gmail (an app password): Inbox notices are also emailed.
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
    )
    if test_config:
        app.config.update(test_config)
    if on_render:
        # Render's proxy talks HTTPS to the visitor and HTTP to us. This makes
        # Flask believe the X-Forwarded-Proto header, so invite links start with https://.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    if on_render and app.config["SECRET_KEY"] == DEV_SECRET_KEY:
        raise RuntimeError("Set the SECRET_KEY environment variable on Render.")
    if on_render and not app.config["DATABASE_URL"]:
        # Without it we'd use a SQLite file, and Render forgets files on every restart.
        raise RuntimeError("Set the DATABASE_URL environment variable on Render (Neon's connection string).")

    from . import auth, db, formatting
    from .views import admin, audit as audit_views, inbox, main, tickets

    db.init_app(app)
    auth.init_app(app)
    formatting.init_app(app)
    # auth comes first: its before_request (load_user) must run before the others'.
    for blueprint in (auth.bp, main.bp, tickets.bp, inbox.bp, audit_views.bp, admin.bp):
        app.register_blueprint(blueprint)

    audit_views.init_app(app)
    app.jinja_env.globals["APP_NAME"] = APP_NAME
    from .models import A_ROLE, PRIORITY_NAMES, ROLE_NAMES, STATUS_NAMES
    app.jinja_env.globals.update(STATUS_NAMES=STATUS_NAMES, PRIORITY_NAMES=PRIORITY_NAMES, ROLE_NAMES=ROLE_NAMES,
                                 A_ROLE=A_ROLE)
    from . import tickets as ticket_rules
    app.jinja_env.globals["unassigned_count"] = ticket_rules.unassigned_count
    from . import notify
    app.jinja_env.globals["unread_count"] = notify.unread_count

    @app.after_request
    def send_emails(response):
        # Emails go out after the change is saved (notify.py's outbox). Never let
        # a mail problem spoil the page the person is waiting for.
        from flask import g
        if g.pop("emails_waiting", False):
            try:
                notify.deliver(db.session())
            except Exception:  # noqa: BLE001
                app.logger.exception("Sending emails failed; /tasks/tick will try again.")
        return response

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.get("/favicon.ico")
    def favicon():
        # Some browsers ask for it at the root, whatever the page says.
        return app.send_static_file("icons/favicon.ico")

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")  # trust our file types
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")  # no showing us inside other sites
        if on_render:
            # Always use HTTPS for this site from now on (for a year).
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    def error_page(code, message):
        return render_template("error.html", code=code, message=message), code

    @app.errorhandler(400)
    def bad_request(error):
        return error_page(400, error.description or "Something in that request was wrong.")

    @app.errorhandler(403)
    def forbidden(error):
        return error_page(403, error.description or "You can't do that.")

    @app.errorhandler(404)
    def not_found(error):
        return error_page(404, "We couldn't find that page.")

    @app.errorhandler(409)
    def conflict(error):
        return error_page(409, error.description or "That can't be done right now.")

    @app.errorhandler(413)
    def too_big(error):
        return error_page(413, "That is too big to send. Pictures can be at most 5 MB each.")

    @app.errorhandler(503)
    def unavailable(error):
        return error_page(503, error.description or "Please try again in a minute.")

    return app
