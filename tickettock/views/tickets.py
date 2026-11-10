"""Ticket pages: New Ticket and My Tickets (customers), the Queue (staff), and one ticket.

Every view loads the ticket with tickets.get_visible() (404 if you can't see
it) and asks permissions.py before changing anything (403 if you may not).
Forms that change a ticket carry the version the page was drawn from: if the
ticket changed since, the change is refused and the page shows the new state.
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import audit, db, desks, sla, tick, tickets, workflow
from .. import permissions as can
from ..auth import login_required, require
from ..dates import utcnow
from ..models import (CUSTOMER_PRIORITIES, PRIORITIES, PRIORITY_NAMES, RESOLVED, STATUS_NAMES, WAITING, Category,
                      User)
from ..workflow import MoveNotAllowed, TicketChanged

bp = Blueprint("tickets", __name__)

STALE = "This ticket changed while you were looking at it. Here is the new version: please check and try again."


def _load(number):
    s = db.session()
    return s, tickets.get_visible(s, g.user, number)


def _back(ticket, anchor=None):
    return redirect(url_for("tickets.show", number=ticket.number, _anchor=anchor))


def _fresh(ticket):
    """False (and a message) if the form was drawn from an older version of the ticket."""
    sent = request.form.get("version", "")
    if sent.isdigit() and int(sent) == ticket.version:
        return True
    flash(STALE, "error")
    return False


def _save(s, ticket, change, *args):
    """Run a change, commit, and turn a lost race into a friendly message."""
    try:
        changed = change(s, ticket, g.user, *args, utcnow())
        s.commit()
        return changed
    except TicketChanged as error:
        s.rollback()
        flash(error.message, "error")
        return None


# ---------- Customers ----------

@bp.get("/tickets")
@login_required
def mine():
    if can.is_staff(g.user):
        return redirect(url_for("tickets.queue"))
    still_open, finished = tickets.for_customer(db.session(), g.user)
    return render_template("tickets/mine.html", still_open=still_open, finished=finished)


@bp.route("/tickets/new", methods=["GET", "POST"])
@login_required
def new():
    require(not can.is_staff(g.user), "Tickets come from customers. Staff answer them in the Queue.")
    s = db.session()
    categories = desks.categories(s, g.desk)
    errors = {}
    form = {"subject": "", "description": "", "category": "", "priority": "normal"}
    if request.method == "POST":
        form = {key: request.form.get(key, "").strip() for key in form}
        errors = tickets.form_problems(form["subject"], form["description"])
        if form["priority"] not in CUSTOMER_PRIORITIES:
            errors["priority"] = "Choose how much it affects you."
        category = None
        if form["category"]:
            category = next((c for c in categories if str(c.id) == form["category"]), None)
            if category is None:
                errors["category"] = "Choose a category from the list."
        if not errors:
            ticket = tickets.create(s, g.user, form["subject"], form["description"], utcnow(),
                                    category=category, priority=form["priority"])
            s.commit()
            flash(f"Thank you! Your ticket is {ticket.key}. We'll reply here, so check back soon.")
            return redirect(url_for("tickets.show", number=ticket.number))
    return render_template("tickets/new.html", categories=categories, errors=errors, form=form), \
        400 if errors else 200


# ---------- Staff ----------

@bp.get("/queue")
@login_required
def queue():
    require(can.is_staff(g.user), "The queue is for the support team.")
    s = db.session()
    now = utcnow()
    tick.run_if_due(s, g.desk, now)
    tab = request.args.get("tab", "unassigned")
    if tab not in tickets.QUEUE_TABS:
        tab = "unassigned"
    q = request.args.get("q", "").strip()[:100]
    category = request.args.get("category", "")
    priority = request.args.get("priority", "")
    page = max(1, request.args.get("page", 1, type=int) or 1)
    filters = []
    rule = tickets.search_filter(q)
    if rule is not None:
        filters.append(rule)
    if category.isdigit():
        filters.append(tickets.Ticket.category_id == int(category))
    if priority in PRIORITIES:
        filters.append(tickets.Ticket.priority == priority)
    rows, total = tickets.queue(s, g.user, tab, filters, page)
    return render_template("tickets/queue.html", rows=rows, total=total, tab=tab, q=q, category=category,
                           priority=priority, page=page, pages=max(1, -(-total // tickets.PAGE_SIZE)),
                           counts=tickets.tab_counts(s, g.user), tab_titles=tickets.TAB_TITLES,
                           categories=desks.categories(s, g.desk, active_only=False),
                           priority_names=PRIORITY_NAMES, sla_ctx=sla.Context(s, g.desk, now))


# ---------- One ticket ----------

@bp.get("/tickets/<int:number>")
@login_required
def show(number):
    s, ticket = _load(number)
    now = utcnow()
    moves = [(to, workflow.move_label(ticket, to, not g.user.is_staff))
             for to in workflow.TRANSITIONS[ticket.status] if to != RESOLVED and can.can_move(g.user, ticket, to, now)]
    return render_template(
        "tickets/ticket.html", ticket=ticket, messages=tickets.messages_for(s, g.user, ticket), moves=moves,
        can_resolve=RESOLVED in workflow.TRANSITIONS[ticket.status] and can.can_move(g.user, ticket, RESOLVED, now),
        can_wait=can.can_move(g.user, ticket, WAITING, now) and WAITING in workflow.TRANSITIONS[ticket.status],
        can_reply=can.can_reply(g.user, ticket, now), reopen_open=can.reopen_window_open(ticket, now),
        reopen_until=ticket.resolved_at + can.REOPEN_WINDOW if ticket.resolved_at else None,
        staff=tickets.staff_on(s, g.desk) if can.can_assign(g.user, ticket) else [],
        categories=tickets.category_choices(s, g.desk, ticket.category_id),
        priorities=PRIORITIES, priority_names=PRIORITY_NAMES, status_names=STATUS_NAMES,
        history=audit.for_ticket(s, ticket, can.is_staff(g.user)),
        clocks=sla.Context(s, g.desk, now).clocks(ticket) if can.is_staff(g.user) else (None, None))


@bp.post("/tickets/<int:number>/reply")
@login_required
def reply(number):
    s, ticket = _load(number)
    now = utcnow()
    internal = request.form.get("internal") == "1"
    if internal:
        require(can.can_add_note(g.user, ticket), "Internal notes are for the support team.")
    else:
        require(can.can_reply(g.user, ticket, now), "This ticket is closed, so it can't have new replies.")
    body = request.form.get("body", "").strip()
    if not body:
        flash("Write something first.", "error")
        return _back(ticket, "reply")
    if len(body) > tickets.BODY_MAX:
        abort(400, f"Keep a reply under {tickets.BODY_MAX} characters.")
    then_wait = request.form.get("then") == "waiting" and not internal
    try:
        tickets.reply(s, ticket, g.user, body, now, internal=internal)
        if then_wait and can.can_move(g.user, ticket, WAITING, now) and not workflow.move_problem(ticket, WAITING):
            workflow.move(s, ticket, WAITING, g.user, now)
        s.commit()
    except TicketChanged as error:
        s.rollback()
        flash(error.message, "error")
        return _back(ticket)
    flash("Note added. Only the team can see it." if internal
          else "Reply sent. The ticket now waits for the customer." if then_wait else "Reply sent.")
    return _back(ticket, "conversation")


@bp.post("/tickets/<int:number>/take")
@login_required
def take(number):
    s, ticket = _load(number)
    require(can.can_take(g.user, ticket), "Only an unassigned ticket can be taken.")
    if _fresh(ticket) and _save(s, ticket, tickets.assign, g.user):
        flash(f"{ticket.key} is yours now.")
    return _back(ticket)


@bp.post("/tickets/<int:number>/drop")
@login_required
def drop(number):
    s, ticket = _load(number)
    require(can.can_drop(g.user, ticket), "You can only hand back a ticket that is yours.")
    if _fresh(ticket) and _save(s, ticket, tickets.assign, None):
        flash(f"{ticket.key} is back in the queue.")
    return _back(ticket)


@bp.post("/tickets/<int:number>/assign")
@login_required
def assign(number):
    s, ticket = _load(number)
    require(can.can_assign(g.user, ticket), "Only a Team Lead or an Admin can give tickets to others.")
    chosen = request.form.get("assignee_id", "")
    assignee = None
    if chosen:
        assignee = s.get(User, int(chosen)) if chosen.isdigit() else None
        if assignee is None or assignee.desk_id != g.desk.id or not assignee.is_staff or not assignee.is_active:
            abort(400, "Choose someone from the support team.")
    if _fresh(ticket) and _save(s, ticket, tickets.assign, assignee):
        flash(f"{ticket.key} is now with {assignee.name}." if assignee else f"{ticket.key} is back in the queue.")
    return _back(ticket)


@bp.post("/tickets/<int:number>/priority")
@login_required
def priority(number):
    s, ticket = _load(number)
    require(can.can_triage(g.user, ticket), "You can't change this ticket's priority.")
    chosen = request.form.get("priority", "")
    if chosen not in PRIORITIES:
        abort(400, "Choose a priority from the list.")
    if _fresh(ticket) and _save(s, ticket, tickets.set_priority, chosen):
        flash(f"Priority is now {PRIORITY_NAMES[chosen]}.")
    return _back(ticket)


@bp.post("/tickets/<int:number>/category")
@login_required
def category(number):
    s, ticket = _load(number)
    require(can.can_triage(g.user, ticket), "You can't change this ticket's category.")
    chosen = request.form.get("category_id", "")
    category_row = None
    if chosen:
        category_row = desks.get_in_desk(s, Category, int(chosen) if chosen.isdigit() else None, g.desk)
    if _fresh(ticket) and _save(s, ticket, tickets.set_category, category_row):
        flash(f"Category is now {category_row.name}." if category_row else "The category was taken off.")
    return _back(ticket)


@bp.post("/tickets/<int:number>/move")
@login_required
def move(number):
    s, ticket = _load(number)
    now = utcnow()
    to = request.form.get("to", "")
    if to not in workflow.TRANSITIONS:
        abort(400, "Choose a status from the list.")
    require(can.can_move(g.user, ticket, to, now), "You can't move this ticket there.")
    if not _fresh(ticket):
        return _back(ticket)
    try:
        workflow.move(s, ticket, to, g.user, now, resolution=request.form.get("resolution", ""),
                      note=request.form.get("note", "").strip()[:300])
        s.commit()
    except MoveNotAllowed as problem:
        flash(str(problem), "error")
        return _back(ticket)
    except TicketChanged as error:
        flash(error.message, "error")
        return _back(ticket)
    flash(f"{ticket.key} is now {STATUS_NAMES[to]}.")
    return _back(ticket)


@bp.post("/tickets/<int:number>/escalate")
@login_required
def escalate(number):
    s, ticket = _load(number)
    require(can.can_escalate(g.user, ticket), "You can't escalate this ticket.")
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Say what help you need, so the Team Lead knows.", "error")
        return _back(ticket)
    if _fresh(ticket):
        if tick.escalate(s, ticket, utcnow(), reason, actor=g.user):
            s.commit()
            flash("The Team Leads were asked to help.")
        else:
            s.rollback()
            flash(TicketChanged.message, "error")
    return _back(ticket)


@bp.post("/tickets/<int:number>/clear-escalation")
@login_required
def clear_escalation(number):
    s, ticket = _load(number)
    require(can.can_clear_escalation(g.user, ticket), "Only a Team Lead can mark an escalation as handled.")
    if _fresh(ticket) and _save(s, ticket, tick.clear_escalation):
        flash("The escalation is marked as handled.")
    return _back(ticket)
