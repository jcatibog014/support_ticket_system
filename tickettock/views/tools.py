"""Agent tools pages: Saved Replies (Team Leads keep them), saved queue views, and bulk changes."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import audit, db, desks, tickets, tools
from .. import permissions as can
from ..auth import login_required, require
from ..dates import utcnow
from ..models import SavedReply, SavedView, Ticket

bp = Blueprint("tools", __name__)


# ---------- Saved replies ----------

@bp.route("/saved-replies", methods=["GET", "POST"])
@login_required
def saved_replies():
    require(can.can_use_saved_replies(g.user), "Saved replies are for the support team.")
    s = db.session()
    errors = {}
    title = body = ""
    if request.method == "POST":
        require(can.can_manage_saved_replies(g.user), "Only a Team Lead or an Admin can add saved replies.")
        title, body = request.form.get("title", "").strip(), request.form.get("body", "").strip()
        errors = tools.reply_problems(title, body)
        if not errors:
            s.add(SavedReply(desk_id=g.desk.id, title=title, body=body, created_by_id=g.user.id, created_at=utcnow()))
            try:
                audit.log(s, g.desk.id, g.user, audit.REPLY_SAVED, utcnow(), new=title)
                s.commit()
            except IntegrityError:
                s.rollback()
                errors["title"] = "There is already a saved reply with that title."
            else:
                flash(f"Saved the reply {title}.")
                return redirect(url_for("tools.saved_replies"))
    return render_template("tools/saved_replies.html", replies=tools.saved_replies(s, g.desk), errors=errors,
                           title=title, body=body, placeholders=tools.PLACEHOLDERS), 400 if errors else 200


@bp.post("/saved-replies/<int:reply_id>")
@login_required
def edit_saved_reply(reply_id):
    require(can.can_manage_saved_replies(g.user), "Only a Team Lead or an Admin can change saved replies.")
    s = db.session()
    reply = desks.get_in_desk(s, SavedReply, reply_id, g.desk)
    if request.form.get("action") == "delete":
        audit.log(s, g.desk.id, g.user, audit.REPLY_REMOVED, utcnow(), old=reply.title)
        s.delete(reply)
        s.commit()
        flash(f"Deleted the saved reply {reply.title}.")
        return redirect(url_for("tools.saved_replies"))
    title, body = request.form.get("title", "").strip(), request.form.get("body", "").strip()
    errors = tools.reply_problems(title, body)
    if errors:
        abort(400, next(iter(errors.values())))
    if (title, body) != (reply.title, reply.body):
        audit.log(s, g.desk.id, g.user, audit.REPLY_CHANGED, utcnow(), old=reply.title, new=title)
    reply.title, reply.body = title, body
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        abort(409, "There is already a saved reply with that title.")
    flash(f"Saved {title}.")
    return redirect(url_for("tools.saved_replies"))


# ---------- Saved queue views ----------

@bp.post("/queue/views")
@login_required
def save_view():
    require(can.is_staff(g.user), "The queue is for the support team.")
    s = db.session()
    name = request.form.get("name", "").strip()[:tools.VIEW_NAME_MAX]
    query = tools.clean_query(request.form)
    if not name:
        flash("Give the view a name.", "error")
        return redirect(url_for("tickets.queue") + ("?" + query if query else ""))
    count = s.scalar(select(func.count()).select_from(SavedView).where(SavedView.user_id == g.user.id))
    if count >= tools.MAX_VIEWS:
        flash(f"You can keep {tools.MAX_VIEWS} views. Delete one first.", "error")
        return redirect(url_for("tickets.queue") + ("?" + query if query else ""))
    s.add(SavedView(user_id=g.user.id, name=name, query=query))
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        flash("You already have a view with that name.", "error")
    else:
        flash(f"Saved the view {name}.")
    return redirect(url_for("tickets.queue") + ("?" + query if query else ""))


@bp.post("/queue/views/<int:view_id>/delete")
@login_required
def delete_view(view_id):
    s = db.session()
    view = s.get(SavedView, view_id)
    if view is None or view.user_id != g.user.id:
        abort(404)
    s.delete(view)
    s.commit()
    flash(f"Deleted the view {view.name}.")
    return redirect(url_for("tickets.queue"))


# ---------- Bulk changes ----------

@bp.post("/queue/bulk")
@login_required
def bulk():
    require(can.can_bulk_edit(g.user), "Only a Team Lead or an Admin can change many tickets at once.")
    s = db.session()
    numbers = sorted({int(n) for n in request.form.getlist("ticket") if n.isdigit()})[:200]
    back = request.form.get("back", "")
    back = back if back.startswith("/queue") and "//" not in back else url_for("tickets.queue")
    if not numbers:
        flash("Tick at least one ticket first.", "error")
        return redirect(back)
    rows = s.scalars(select(Ticket).where(tickets.visible_filter(g.user), Ticket.number.in_(numbers))
                     .options(*tickets.list_options()).order_by(Ticket.number)).all()
    action = request.form.get("action", "")
    value = request.form.get("assignee_id" if action == "assign" else "priority", "")
    try:
        done, skipped = tickets.bulk(s, g.user, rows, action, value, utcnow())
    except ValueError as problem:
        flash(str(problem), "error")
        return redirect(back)
    message = f"Changed {done} of {len(rows)} tickets."
    if skipped:
        message += f" {skipped} could not be changed (finished, already like that, or changed by someone else)."
    flash(message)
    return redirect(back)
