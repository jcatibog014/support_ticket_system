"""The Inbox: everyone's notices about their tickets."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import db, notify
from ..auth import login_required
from ..dates import utcnow
from ..models import Notification

bp = Blueprint("inbox", __name__)


@bp.get("/inbox")
@login_required
def index():
    show = request.args.get("show", "all")
    if show not in notify.FILTERS:
        show = "all"
    s = db.session()
    notices = notify.inbox(s, g.user, only_unread=show == "unread")
    return render_template("inbox.html", notices=notices, show=show, filters=notify.FILTERS, words=notify.words)


@bp.post("/inbox/<int:notice_id>/open")
@login_required
def open_notice(notice_id):
    """Mark it read and go to its ticket."""
    s = db.session()
    notice = s.get(Notification, notice_id)
    if notice is None or notice.user_id != g.user.id:
        abort(404)
    if notice.read_at is None:
        notice.read_at = utcnow()
        s.commit()
    if notice.ticket is None:
        return redirect(url_for("inbox.index"))
    return redirect(url_for("tickets.show", number=notice.ticket.number))


@bp.post("/inbox/read")
@login_required
def mark_all():
    s = db.session()
    notify.mark_all_read(s, g.user, utcnow())
    s.commit()
    flash("Everything is marked as read.")
    return redirect(url_for("inbox.index"))
