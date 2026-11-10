"""Agent tools: saved replies, saved queue views, and "who else has this ticket open".

Merging tickets is in workflow.py (it closes a ticket), round-robin and bulk
changes are in tickets.py (they assign and move tickets).
"""
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .models import SavedReply, SavedView, TicketView

PLACEHOLDERS = {"{name}": "the customer's first name", "{agent}": "your first name", "{ticket}": "the ticket number"}
TITLE_MAX, BODY_MAX = 80, 5000
VIEW_NAME_MAX, MAX_VIEWS = 40, 12
# The queue filters a saved view keeps (and nothing else from the address).
VIEW_KEYS = ("tab", "q", "category", "priority")
LOOKING = timedelta(seconds=90)  # someone who opened the ticket this recently is "also looking"


# ---------- Saved replies ----------

def saved_replies(s, desk):
    return s.scalars(select(SavedReply).where(SavedReply.desk_id == desk.id).order_by(SavedReply.title)).all()


def fill(body, ticket, agent):
    """Put the real names in. Plain replace, so a stray { in a reply can't break anything."""
    for key, value in (("{name}", ticket.customer.first_name), ("{agent}", agent.first_name), ("{ticket}", ticket.key)):
        body = body.replace(key, value)
    return body


def reply_problems(title, body):
    errors = {}
    if not title or len(title) > TITLE_MAX:
        errors["title"] = f"Use 1 to {TITLE_MAX} characters."
    if not body or len(body) > BODY_MAX:
        errors["body"] = f"Use 1 to {BODY_MAX} characters."
    return errors


# ---------- Saved views ----------

def clean_query(args):
    """Only the queue's own filters, in a fixed order: "tab=open&priority=urgent"."""
    return urlencode([(key, args.get(key)) for key in VIEW_KEYS if args.get(key)])


def views_for(s, user):
    return s.scalars(select(SavedView).where(SavedView.user_id == user.id).order_by(SavedView.name)).all()


def view_args(view):
    return dict(parse_qsl(view.query))


# ---------- Who else is looking ----------

def seen(s, ticket, user, now):
    """Remember that this person has the ticket open (the caller commits)."""
    done = s.execute(update(TicketView).where(TicketView.ticket_id == ticket.id, TicketView.user_id == user.id)
                     .values(seen_at=now).execution_options(synchronize_session=False))
    if done.rowcount == 0:
        try:
            with s.begin_nested():  # two tabs at once: the second insert just loses
                s.add(TicketView(ticket_id=ticket.id, user_id=user.id, seen_at=now))
        except IntegrityError:
            pass


def others_looking(s, ticket, user, now):
    """The other staff who had this ticket open in the last 90 seconds."""
    rows = s.scalars(select(TicketView).where(TicketView.ticket_id == ticket.id, TicketView.user_id != user.id,
                                              TicketView.seen_at >= now - LOOKING)
                     .options(selectinload(TicketView.user))).all()
    return sorted((row.user for row in rows if row.user.is_active), key=lambda person: person.name)
