"""The Inbox: telling people what happened to their tickets (and, if set up, emailing them).

send() writes a Notification row inside the same transaction as the change.
If the change rolls back, so does the notice. deliver() emails the waiting
ones after the request has committed; a failed email is tried again (3 times
in all, also by /tasks/tick) and never undoes anything.

Who hears what:
  - the customer: the team's public replies, and their ticket waiting on them,
    resolved, closed or cancelled by someone else;
  - the assignee: the customer's replies, a colleague's internal note, the ticket
    given to them, and their ticket reopened, closed or cancelled by someone else;
  - Team Leads: escalations (sla.py).
Never the person who did it, and never someone who can't open the ticket.
"""
from flask import g, has_request_context, url_for
from sqlalchemy import func, select, update

from . import mailer
from . import permissions as can
from .models import CANCELLED, CLOSED, OPEN, RESOLVED, STATUS_NAMES, WAITING, Notification

MAX_TRIES = 3
FILTERS = {"all": "All", "unread": "Unread"}


def send(s, user, ticket, kind, actor, now, detail=""):
    """One Inbox notice (the caller commits). Returns it, or None if nobody should get it."""
    if user is None or not user.is_active or (actor is not None and actor.id == user.id):
        return None
    if ticket is not None and not can.can_view_ticket(user, ticket):
        return None
    wants_email = user.email_updates and not user.is_demo and mailer.enabled()
    notice = Notification(desk_id=user.desk_id, user_id=user.id, ticket_id=ticket.id if ticket else None, kind=kind,
                          actor_name=actor.name if actor is not None else "Ticket Tock", detail=(detail or "")[:200],
                          created_at=now, email_status="waiting" if wants_email else "")
    notice.user = user
    notice.ticket = ticket
    s.add(notice)
    if wants_email and has_request_context():
        g.emails_waiting = True
    return notice


def after_reply(s, ticket, author, body, internal, now):
    first_words = " ".join(body.split())[:120]
    if internal:
        send(s, ticket.assignee, ticket, "note", author, now)
    elif can.is_staff(author):
        send(s, ticket.customer, ticket, "reply", author, now, first_words)
    else:
        send(s, ticket.assignee, ticket, "reply", author, now, first_words)


def after_move(s, ticket, before, to, actor, now):
    if to in (WAITING, RESOLVED, CLOSED, CANCELLED):
        send(s, ticket.customer, ticket, "status", actor, now, to)
    if to in (CLOSED, CANCELLED) or (before == RESOLVED and to == OPEN):
        send(s, ticket.assignee, ticket, "status", actor, now, to)


def after_assign(s, ticket, assignee, actor, now):
    send(s, assignee, ticket, "assigned", actor, now)


# ---------- Reading ----------

def unread_count():
    """For the sidebar (a Jinja global)."""
    from . import db
    if g.get("user") is None:
        return 0
    return db.session().scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == g.user.id, Notification.read_at.is_(None)))


def inbox(s, user, only_unread=False, limit=100):
    query = select(Notification).where(Notification.user_id == user.id)
    if only_unread:
        query = query.where(Notification.read_at.is_(None))
    return s.scalars(query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)).all()


def mark_all_read(s, user, now):
    s.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
              .values(read_at=now))


# ---------- Email ----------

def words(notice):
    """(subject, one sentence) for an email; the Inbox uses the same sentence."""
    key = f"[{notice.ticket.key}] " if notice.ticket else ""
    subject = notice.ticket.subject if notice.ticket else ""
    who = notice.actor_name
    if notice.kind == "reply":
        return f"{key}New reply: {subject}", f"{who} replied: “{notice.detail}”"
    if notice.kind == "note":
        return f"{key}Internal note: {subject}", f"{who} added an internal note."
    if notice.kind == "assigned":
        return f"{key}Assigned to you: {subject}", f"{who} gave this ticket to you."
    if notice.kind == "status":
        status = STATUS_NAMES.get(notice.detail, notice.detail)
        return f"{key}{status}: {subject}", f"{who} changed the status to {status}."
    if notice.kind == "escalated":
        return f"{key}Escalated: {subject}", notice.detail or "This ticket missed its time limit."
    if notice.kind == "rated":
        return f"{key}Rated: {subject}", f"{who} rated the help {notice.detail}."
    if notice.kind == "merged":
        return f"{key}Merged: {subject}", f"{who} merged it into {notice.detail}."
    return f"{key}{subject}", who


def deliver(s, limit=50):
    """Email the waiting notices. Returns how many were sent."""
    sent = 0
    waiting = s.scalars(select(Notification).where(Notification.email_status == "waiting")
                        .order_by(Notification.id).limit(limit)).all()
    for notice in waiting:
        subject, sentence = words(notice)
        link = url_for("tickets.show", number=notice.ticket.number, _external=True) if notice.ticket else ""
        body = (f"Hello {notice.user.first_name},\n\n{sentence}\n\n" + (f"Open the ticket: {link}\n\n" if link else "")
                + "Ticket Tock\nYou can turn these emails off in Settings.\n")
        notice.email_tries += 1
        try:
            mailer.send(notice.user.email, subject, body)
        except Exception:  # noqa: BLE001  (a mail server can fail in many ways; we try again later)
            if notice.email_tries >= MAX_TRIES:
                notice.email_status = "failed"
        else:
            notice.email_status = "sent"
            sent += 1
        s.commit()
    return sent
