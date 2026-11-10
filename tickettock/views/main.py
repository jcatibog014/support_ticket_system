"""Home: where everyone starts after logging in.

Customers go straight to their tickets. Staff get a short summary: what is
waiting in the queue, and the tickets that are theirs.
"""
from datetime import datetime, time

from flask import Blueprint, g, redirect, render_template, url_for
from sqlalchemy import func, select

from .. import db, sla, tick, tickets
from .. import permissions as can
from ..auth import login_required
from ..dates import from_local, today, utcnow
from ..models import Ticket

bp = Blueprint("main", __name__)


@bp.get("/")
@login_required
def home():
    if not can.is_staff(g.user):
        return redirect(url_for("tickets.mine"))
    s = db.session()
    tick.run_if_due(s, g.desk, utcnow())
    counts = tickets.tab_counts(s, g.user)
    mine, _ = tickets.queue(s, g.user, "mine", [])
    since = from_local(datetime.combine(today(), time()))  # midnight in the Philippines
    resolved_today = s.scalar(select(func.count()).select_from(Ticket).where(
        tickets.visible_filter(g.user), Ticket.resolved_at >= since))
    return render_template("home.html", counts=counts, mine=mine[:10], resolved_today=resolved_today,
                           sla_ctx=sla.Context(s, g.desk, utcnow()))
