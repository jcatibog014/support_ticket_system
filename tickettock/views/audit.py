"""The Audit Log (Team Leads and Admins), its CSV file (Admins), and the chain check."""
import csv
import io
from datetime import date, datetime, time, timedelta

import click
from flask import Blueprint, Response, g, render_template, request
from flask.cli import with_appcontext
from sqlalchemy import func, select

from .. import audit, db, tickets
from .. import permissions as can
from ..auth import login_required, require
from ..dates import from_local, to_local
from ..models import Desk, Ticket

bp = Blueprint("audit", __name__)


def _filters(s):
    """Read the page's filters: (what the form shows, what the query uses)."""
    shown = {key: request.args.get(key, "").strip()[:80] for key in ("q", "who", "what", "ticket", "since", "until")}
    used = {"actor_name": shown["who"], "group": shown["what"], "words": shown["q"]}
    q_number = tickets.parse_key(shown["q"])
    if q_number is not None:
        used["words_ticket_id"] = s.scalar(select(Ticket.id).where(Ticket.desk_id == g.desk.id, Ticket.number == q_number))
    number = tickets.parse_key(shown["ticket"])
    if shown["ticket"]:
        found = s.scalar(select(Ticket.id).where(Ticket.desk_id == g.desk.id, Ticket.number == number)) \
            if number is not None else None
        used["ticket_id"] = found if found is not None else -1  # an unknown ticket finds nothing
    for key, shift in (("since", 0), ("until", 1)):
        try:
            day = date.fromisoformat(shown[key]) if shown[key] else None
        except ValueError:
            day = None
            shown[key] = ""
        if day is not None:
            used[key] = from_local(datetime.combine(day + timedelta(days=shift), time()))
    return shown, used


@bp.get("/audit")
@login_required
def log():
    require(can.can_view_audit_log(g.user), "The Audit Log is for Team Leads and Admins.")
    s = db.session()
    shown, used = _filters(s)
    page = max(1, request.args.get("page", 1, type=int) or 1)
    query = audit.search(s, g.desk, **used)
    total = s.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    events = s.scalars(query.limit(audit.PAGE_SIZE).offset((page - 1) * audit.PAGE_SIZE)).all()
    ok, checked, problem = audit.verify(s, g.desk)
    return render_template("audit.html", events=events, total=total, page=page,
                           pages=max(1, -(-total // audit.PAGE_SIZE)), shown=shown, groups=audit.GROUPS,
                           names=audit.actor_names(s, g.desk), chain_ok=ok, checked=checked, problem=problem)


@bp.get("/audit.csv")
@login_required
def export():
    require(can.can_export_audit_log(g.user), "Only an Admin can download the Audit Log.")
    s = db.session()
    _, used = _filters(s)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["line", "time (Philippines)", "who", "role", "what", "ticket", "field", "before", "after",
                     "note", "internal", "hash"])
    for event in s.scalars(audit.search(s, g.desk, **used)):
        writer.writerow([event.seq, to_local(event.created_at).strftime("%Y-%m-%d %H:%M:%S"), event.actor_name,
                         event.actor_role, event.verb, event.ticket.key if event.ticket else "", event.field,
                         event.old_value, event.new_value, event.note, "yes" if event.is_internal else "", event.hash])
    # A BOM first, so Excel reads the names with accents right.
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=ticket-tock-audit-log.csv"})


@click.command("verify-audit")
@with_appcontext
def verify_audit_command():
    """Check every desk's history chain. Exits with an error if any line was changed."""
    s = db.session()
    broken = 0
    for desk in s.scalars(select(Desk).order_by(Desk.id)):
        ok, checked, problem = audit.verify(s, desk)
        label = f"{desk.name}{' (demo)' if desk.is_demo else ''}"
        click.echo(f"{label}: {'Chain OK' if ok else 'BROKEN'} ({checked} lines checked){'' if ok else ': ' + problem}")
        broken += not ok
    if broken:
        raise click.ClickException(f"{broken} desk(s) have a broken history chain.")


def init_app(app):
    app.cli.add_command(verify_audit_command)
