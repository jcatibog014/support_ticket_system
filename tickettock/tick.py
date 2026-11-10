"""The timer: what should happen even when nobody does anything.

Each run, for every desk:
  1. escalate tickets that went past a time limit (sla.py): the Team Leads,
     Admins and the assignee get an Inbox notice, and the queue's Overdue tab
     shows them;
  2. close tickets that stayed Resolved for 7 days (stage 6);
then email the waiting Inbox notices.

Render's free plan has no timer and sleeps when idle, so a free GitHub Actions
schedule (.github/workflows/tick.yml) calls POST /tasks/tick every 15 minutes
with a secret key (TICK_KEY). Staff opening Home or the Queue also runs it for
their desk, at most every few minutes (run_if_due), so the demo and a desk
without the schedule still escalate. From the computer: flask --app tickettock tick.

Running it twice at once is safe: escalate() only touches a ticket whose
escalated_at is still empty (the version check), so nobody is told twice.
"""
import hmac
from datetime import timedelta

import click
from flask import Blueprint, abort, current_app, jsonify, request
from flask.cli import with_appcontext
from sqlalchemy import or_, select, update

from . import audit, db, notify, workflow
from .models import LEAD, RANK, STAFF_ROLES, Desk, Ticket, User
from .sla import overdue_filter

bp = Blueprint("tick", __name__)

EVERY = timedelta(minutes=3)  # run_if_due: at most this often per desk


def escalate(s, ticket, now, reason, actor=None):
    """Call in the Team Leads. Returns False if someone escalated it a moment ago."""
    if ticket.escalated_at is not None:
        return False
    try:
        workflow.apply(s, ticket, now, escalated_at=now, escalation_reason=reason[:300])
    except workflow.TicketChanged:
        return False
    audit.log(s, ticket.desk_id, actor, audit.ESCALATED, now, ticket=ticket, note=reason, internal=True)
    leads = s.scalars(select(User).where(User.desk_id == ticket.desk_id, User.role.in_(STAFF_ROLES),
                                         User.is_active.is_(True))).all()
    for person in leads:
        if RANK[person.role] >= RANK[LEAD] or person.id == ticket.assignee_id:
            notify.send(s, person, ticket, "escalated", actor, now, reason)
    return True


def clear_escalation(s, ticket, actor, now):
    workflow.apply(s, ticket, now, escalated_at=None, escalation_reason="")
    audit.log(s, ticket.desk_id, actor, audit.DE_ESCALATED, now, ticket=ticket, internal=True)
    return True


def reason_for(ticket, now):
    if ticket.first_response_at is None and ticket.response_due_at and ticket.response_due_at < now:
        return "Nobody has replied yet, and the first reply is late."
    return "It isn't solved yet, and the time to solve it has run out."


def run_desk(s, desk, now):
    """One desk's work. Returns how many tickets were escalated."""
    escalated = 0
    late = s.scalars(select(Ticket).where(Ticket.desk_id == desk.id, Ticket.escalated_at.is_(None),
                                          overdue_filter(now)).order_by(Ticket.id)).all()
    for ticket in late:
        if escalate(s, ticket, now, reason_for(ticket, now)):
            escalated += 1
            s.commit()
    from . import closing
    closed = closing.close_old(s, desk, now)
    s.execute(update(Desk).where(Desk.id == desk.id).values(last_tick_at=now)
              .execution_options(synchronize_session=False))
    s.commit()
    return escalated, closed


def run(s, now):
    from .auth import purge_demos
    report = {"escalated": 0, "closed": 0, "demos_deleted": purge_demos(s, now)}
    s.commit()
    desks = s.scalars(select(Desk).where(or_(Desk.is_demo.is_(False), Desk.demo_expires_at > now))).all()
    for desk in desks:
        escalated, closed = run_desk(s, desk, now)
        report["escalated"] += escalated
        report["closed"] += closed
    report["emails"] = notify.deliver(s)
    return report


def run_if_due(s, desk, now):
    """Run for one desk if the timer hasn't lately (Home and the Queue call this)."""
    if desk.last_tick_at is None or desk.last_tick_at <= now - EVERY:
        run_desk(s, desk, now)


@bp.post("/tasks/tick")
def tick():
    key = current_app.config.get("TICK_KEY") or ""
    sent = request.headers.get("X-Tick-Key", "")
    if not key or not hmac.compare_digest(sent, key):
        abort(404)
    from .dates import utcnow
    return jsonify(run(db.session(), utcnow()))


@click.command("tick")
@with_appcontext
def tick_command():
    """Run the timer once (escalations, closing old resolved tickets, emails)."""
    from .dates import utcnow
    base = current_app.config.get("BASE_URL") or "http://127.0.0.1:5010"
    with current_app.test_request_context(base_url=base):  # links in emails need the site's address
        report = run(db.session(), utcnow())
    click.echo(", ".join(f"{name}: {value}" for name, value in report.items()))


def init_app(app):
    app.config.setdefault("CSRF_EXEMPT", set()).add("tick.tick")  # it has its own key instead
    app.cli.add_command(tick_command)
