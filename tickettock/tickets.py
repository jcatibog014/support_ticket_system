"""Tickets: making them, finding the ones a person may see, replies, and changes.

Every list starts from visible_filter(user), so a customer never sees anyone
else's ticket, and nobody sees another desk's. A single ticket is loaded with
get_visible(), which gives 404 for a ticket you can't see.

Changes go through workflow.apply() (the version check) and write a history
line for every field that really changed.
"""
from flask import abort
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import selectinload

from . import audit, hours, notify, sla, workflow
from . import permissions as can
from .models import (ACTIVE, AGENT, CANCELLED, CLOSED, IN_PROGRESS, NEW, OPEN, PRIORITIES, PRIORITY_NAMES,
                     PRIORITY_RANK, RESOLVED,
                     STAFF_ROLES, STATUS_NAMES, TICKET_PREFIX, URGENT, WAITING, Category, Desk, Message, Ticket, User)

SUBJECT_MAX = 120
BODY_MAX = 5000
PAGE_SIZE = 50

# The staff queue's tabs: (key, title, a WHERE for it).
# In the order a ticket goes: everything open first, then narrower views, the finished ones last.
QUEUE_TABS = ("open", "unassigned", "mine", "overdue", "waiting", "resolved", "closed")
TAB_TITLES = {"unassigned": "Unassigned", "mine": "Assigned to Me", "overdue": "Overdue", "open": "All Open",
              "waiting": "Waiting", "resolved": "Resolved", "closed": "Closed"}


def visible_filter(user):
    """The WHERE for "tickets this person may see"."""
    rule = Ticket.desk_id == user.desk_id
    if not can.is_staff(user):
        rule = rule & (Ticket.customer_id == user.id)
    return rule


def list_options():
    return (selectinload(Ticket.customer), selectinload(Ticket.assignee), selectinload(Ticket.category))


def parse_key(text):
    """'TT-12', 'tt12', '#12' or '12' -> 12, else None."""
    text = (text or "").strip().upper().replace(" ", "")
    for prefix in (TICKET_PREFIX + "-", TICKET_PREFIX, "#"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return int(text) if text.isdigit() and len(text) < 9 else None


def get_visible(s, user, number):
    """The ticket TT-<number>, or 404 if this person may not see it."""
    ticket = s.scalar(select(Ticket).where(Ticket.desk_id == user.desk_id, Ticket.number == number)
                      .options(*list_options()))
    if ticket is None or not can.can_view_ticket(user, ticket):
        abort(404)
    return ticket


def messages_for(s, user, ticket):
    """The conversation as this person may see it: customers never get internal notes."""
    query = select(Message).where(Message.ticket_id == ticket.id).options(selectinload(Message.author))
    if not can.is_staff(user):
        query = query.where(Message.is_internal.is_(False))
    return s.scalars(query.order_by(Message.created_at, Message.id)).all()


def staff_on(s, desk):
    """People a ticket can be given to: active staff."""
    return s.scalars(select(User).where(User.desk_id == desk.id, User.role.in_(STAFF_ROLES),
                                        User.is_active.is_(True)).order_by(User.name)).all()


# ---------- Making a ticket ----------

def form_problems(subject, description):
    errors = {}
    if not subject:
        errors["subject"] = "Say in a few words what is wrong."
    elif len(subject) > SUBJECT_MAX:
        errors["subject"] = f"Use at most {SUBJECT_MAX} characters."
    if not description:
        errors["description"] = "Tell us what happened, and what you expected."
    elif len(description) > BODY_MAX:
        errors["description"] = f"Use at most {BODY_MAX} characters."
    return errors


def next_number(s, desk_id):
    """Raise the desk's counter in the database and take the number. The UPDATE
    locks the desk's row, so two tickets made at once get different numbers."""
    s.execute(update(Desk).where(Desk.id == desk_id).values(next_number=Desk.next_number + 1)
              .execution_options(synchronize_session=False))
    return s.scalar(select(Desk.next_number).where(Desk.id == desk_id)) - 1


def create(s, customer, subject, description, now, category=None, priority="normal"):
    """A new ticket from a customer (the caller commits)."""
    desk = customer.desk
    ticket = Ticket(desk_id=customer.desk_id, number=next_number(s, customer.desk_id), subject=subject,
                    description=description, customer_id=customer.id,
                    category_id=category.id if category else None, priority=priority,
                    created_at=now, updated_at=now, paused_minutes=0,
                    **sla.due_times(desk, now, priority, 0, sla.rules_for(s, desk), hours.holiday_days(s, desk)))
    ticket.customer = customer
    ticket.category = category
    s.add(ticket)
    s.flush()
    audit.log(s, ticket.desk_id, customer, audit.CREATED, now, ticket=ticket, new=subject)
    if category is not None and category.auto_assign:
        auto_assign(s, ticket, category, now)
    return ticket


def auto_assign(s, ticket, category, now):
    """Round-robin: give the ticket to the next active Agent after the one who got the
    last ticket in this category (by id, starting again at the first). Ticket Tock does it."""
    agents = s.scalars(select(User).where(User.desk_id == ticket.desk_id, User.role == AGENT,
                                          User.is_active.is_(True)).order_by(User.id)).all()
    if not agents:
        return None
    after = [agent for agent in agents if category.last_assigned_id is not None and agent.id > category.last_assigned_id]
    chosen = after[0] if after else agents[0]
    s.execute(update(Category).where(Category.id == category.id).values(last_assigned_id=chosen.id)
              .execution_options(synchronize_session=False))
    category.last_assigned_id = chosen.id
    assign(s, ticket, None, chosen, now)
    return chosen


# ---------- Replies ----------

def reply(s, ticket, author, body, now, internal=False):
    """Add a reply (or an internal note) and do what it means for the ticket:

    - the team's first public reply is the "first response", and opens a New ticket;
    - a customer's reply brings a Waiting ticket back to In Progress,
      and reopens a Resolved one (within 7 days; can_reply() checks that).
    """
    message = Message(ticket_id=ticket.id, author_id=author.id, body=body, is_internal=internal, created_at=now)
    message.author = author
    s.add(message)
    notify.after_reply(s, ticket, author, body, internal, now)
    if internal:
        audit.log(s, ticket.desk_id, author, audit.NOTED, now, ticket=ticket, internal=True)
        workflow.touch(s, ticket, now)
        return message
    audit.log(s, ticket.desk_id, author, audit.REPLIED, now, ticket=ticket)
    if can.is_staff(author):
        workflow.touch(s, ticket, now, **({"first_response_at": now} if ticket.first_response_at is None else {}))
        if ticket.escalated_at is not None and ticket.escalation_reason == sla.FIRST_REPLY_LATE:
            # Escalated because nobody had replied: this reply is the answer.
            workflow.apply(s, ticket, now, escalated_at=None, escalation_reason="")
            audit.log(s, ticket.desk_id, author, audit.DE_ESCALATED, now, ticket=ticket, internal=True,
                      note="Cleared by itself: the first reply was sent.")
        if ticket.status == NEW:
            workflow.move(s, ticket, OPEN, author, now)
    else:
        workflow.touch(s, ticket, now)
        if ticket.status == WAITING:
            workflow.move(s, ticket, IN_PROGRESS, None, now, note="The customer replied.")
        elif ticket.status == RESOLVED:
            workflow.move(s, ticket, OPEN, None, now, note="The customer replied, so it was reopened.")
    return message


# ---------- Changes ----------

def assign(s, ticket, actor, assignee, now):
    """Give the ticket to someone (or to nobody). A New ticket that someone
    takes becomes Open: a person is looking at it now."""
    before = ticket.assignee
    if (before.id if before else None) == (assignee.id if assignee else None):
        return False
    workflow.apply(s, ticket, now, assignee_id=assignee.id if assignee else None)
    ticket.assignee = assignee
    audit.log(s, ticket.desk_id, actor, audit.ASSIGNED, now, ticket=ticket, field="assignee",
              old=before.name if before else "", new=assignee.name if assignee else "")
    notify.after_assign(s, ticket, assignee, actor, now)
    if assignee is not None and ticket.status == NEW:
        workflow.move(s, ticket, OPEN, actor, now)
    return True


def set_priority(s, ticket, actor, priority, now):
    if priority == ticket.priority:
        return False
    before = ticket.priority
    # A new priority means new time limits, still counted from when the ticket was sent.
    workflow.apply(s, ticket, now, priority=priority, **sla.targets(s, s.get(Desk, ticket.desk_id), ticket, priority))
    audit.log(s, ticket.desk_id, actor, audit.PRIORITY, now, ticket=ticket, field="priority", old=before, new=priority)
    return True


def set_category(s, ticket, actor, category, now):
    if (ticket.category_id or None) == (category.id if category else None):
        return False
    before = ticket.category.name if ticket.category else ""
    workflow.apply(s, ticket, now, category_id=category.id if category else None)
    ticket.category = category
    audit.log(s, ticket.desk_id, actor, audit.CATEGORY, now, ticket=ticket, field="category",
              old=before, new=category.name if category else "")
    return True


# ---------- Lists ----------

def _priority_order():
    """Urgent first, then High, Normal, Low."""
    return case(*[(Ticket.priority == name, -rank) for name, rank in PRIORITY_RANK.items()], else_=0)


def tab_filter(tab, user, now=None):
    if tab == "overdue":
        # Late on a time limit, or escalated by hand.
        from .dates import utcnow
        return or_(sla.overdue_filter(now or utcnow()), Ticket.escalated_at.is_not(None) & Ticket.status.in_(ACTIVE))
    return {
        "unassigned": Ticket.assignee_id.is_(None) & Ticket.status.in_(ACTIVE),
        "mine": (Ticket.assignee_id == user.id) & Ticket.status.in_(ACTIVE),
        "open": Ticket.status.in_(ACTIVE),
        "waiting": Ticket.status == WAITING,
        "resolved": Ticket.status == RESOLVED,
        "closed": Ticket.status.in_((CLOSED, CANCELLED)),
    }[tab]


def search_filter(q):
    """A ticket number, or words in the subject or the customer's name or email."""
    q = (q or "").strip()
    if not q:
        return None
    number = parse_key(q)
    words = f"%{q.lower()}%"
    rule = or_(func.lower(Ticket.subject).like(words),
               Ticket.customer_id.in_(select(User.id).where(or_(func.lower(User.name).like(words),
                                                               func.lower(User.email).like(words)))))
    return or_(Ticket.number == number, rule) if number is not None else rule


def queue(s, user, tab, filters, page=1):
    """One page of the staff queue, and the total count."""
    query = select(Ticket).where(visible_filter(user), tab_filter(tab, user))
    for rule in filters:
        query = query.where(rule)
    total = s.scalar(select(func.count()).select_from(query.subquery()))
    order = (Ticket.updated_at.desc(),) if tab in ("resolved", "closed") else (_priority_order(), Ticket.created_at)
    rows = s.scalars(query.options(*list_options()).order_by(*order, Ticket.id)
                     .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    return rows, total


def tab_counts(s, user, now=None):
    base = select(func.count()).select_from(Ticket).where(visible_filter(user))
    return {tab: s.scalar(base.where(tab_filter(tab, user, now))) for tab in QUEUE_TABS}


def for_customer(s, user):
    """(still open, finished) for My Tickets, newest change first."""
    rows = s.scalars(select(Ticket).where(visible_filter(user)).options(*list_options())
                     .order_by(Ticket.updated_at.desc(), Ticket.id.desc())).all()
    return [t for t in rows if t.status in ACTIVE or t.status == RESOLVED], \
        [t for t in rows if t.status in (CLOSED, CANCELLED)]


def category_choices(s, desk, current=None):
    """Active categories, plus a hidden one if this ticket still has it."""
    rows = s.scalars(select(Category).where(Category.desk_id == desk.id)
                     .order_by(Category.position, Category.name)).all()
    return [c for c in rows if c.is_active or (current is not None and c.id == current)]


def status_name(status):
    return STATUS_NAMES.get(status, status)


def priority_name(priority):
    return PRIORITY_NAMES.get(priority, priority)


def is_urgent(ticket):
    return ticket.priority == URGENT


def unassigned_count():
    """For the sidebar's Queue link (a Jinja global)."""
    from flask import g

    from . import db
    if g.get("user") is None or not can.is_staff(g.user):
        return 0
    return db.session().scalar(select(func.count()).select_from(Ticket).where(
        visible_filter(g.user), tab_filter("unassigned", g.user)))


# ---------- Many tickets at once (Team Leads) ----------

BULK_ACTIONS = {"assign": "Give to", "priority": "Set priority", "close": "Close resolved tickets"}


def bulk(s, user, rows, action, value, now):
    """Do one change to many tickets. Each ticket is checked on its own (permissions,
    workflow, version) and saved on its own, so one refusal doesn't stop the rest.
    Returns (how many were changed, how many were skipped)."""
    assignee = None
    if action == "assign" and value:
        assignee = s.get(User, int(value)) if str(value).isdigit() else None
        if assignee is None or assignee.desk_id != user.desk_id or not assignee.is_staff or not assignee.is_active:
            raise ValueError("Choose someone from the support team.")
    if action == "priority" and value not in PRIORITIES:
        raise ValueError("Choose a priority from the list.")
    if action not in BULK_ACTIONS:
        raise ValueError("Choose what to do.")
    done = skipped = 0
    for ticket in rows:
        try:
            if action == "assign" and can.can_assign(user, ticket):
                changed = assign(s, ticket, user, assignee, now)
            elif action == "priority" and can.can_triage(user, ticket):
                changed = set_priority(s, ticket, user, value, now)
            elif (action == "close" and can.can_move(user, ticket, CLOSED, now)
                  and workflow.move_problem(ticket, CLOSED) is None):
                workflow.move(s, ticket, CLOSED, user, now)
                changed = True
            else:
                skipped += 1
                continue
            s.commit()
            done += bool(changed)
        except workflow.TicketChanged:
            skipped += 1
    return done, skipped


def rate(s, ticket, customer, stars, comment, now):
    """The customer's rating (permissions.can_rate checks who): saved, in the history, and told to the assignee."""
    workflow.apply(s, ticket, now, rating=stars, rating_comment=comment, rated_at=now)
    audit.log(s, ticket.desk_id, customer, audit.RATED, now, ticket=ticket, field="rating", new=str(stars), note=comment)
    notify.send(s, ticket.assignee, ticket, "rated", customer, now, f"{stars} out of 5")
