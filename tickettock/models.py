"""The data model: every table, as Python classes (SQLAlchemy 2.0).

    desks ──< users             (customers sign up; staff join with an invite)
      ├──< invites              (one-time links that add a staff member with a role)
      ├──< categories           ("Billing", "Login and account", ...)
      ├──< tickets ── customer_id, assignee_id ──> users
      │      └──< messages      (the conversation: the customer's and the team's replies)
      └──< audit_events         (the history: append-only, each row chained to the one before)

There is one real desk (made by "flask create-admin"). Every Try Demo makes
its own small desk, so a visitor's changes never reach the real one. Every row
belongs to a desk, every query starts from one, and a row from another desk
is "not found".

A person's role (customer, agent, team lead, admin) is stored on the user.
What each role may do is in permissions.py, and only there.
"""
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .dates import utcnow
from .db import Base

# Roles, lowest first.
CUSTOMER, AGENT, LEAD, ADMIN = "customer", "agent", "lead", "admin"
ROLES = (CUSTOMER, AGENT, LEAD, ADMIN)
STAFF_ROLES = (AGENT, LEAD, ADMIN)
RANK = {role: number for number, role in enumerate(ROLES)}
ROLE_NAMES = {CUSTOMER: "Customer", AGENT: "Agent", LEAD: "Team Lead", ADMIN: "Admin"}
A_ROLE = {CUSTOMER: "a Customer", AGENT: "an Agent", LEAD: "a Team Lead", ADMIN: "an Admin"}
ROLE_RULE = "role IN ('customer', 'agent', 'lead', 'admin')"

# Ticket statuses (workflow.py says which way each one can go).
NEW, OPEN, IN_PROGRESS, WAITING = "new", "open", "in_progress", "waiting"
RESOLVED, CLOSED, CANCELLED = "resolved", "closed", "cancelled"
STATUSES = (NEW, OPEN, IN_PROGRESS, WAITING, RESOLVED, CLOSED, CANCELLED)
ACTIVE = (NEW, OPEN, IN_PROGRESS, WAITING)  # somebody still has work to do
FINISHED = (CLOSED, CANCELLED)  # nothing more can happen
STATUS_NAMES = {NEW: "New", OPEN: "Open", IN_PROGRESS: "In Progress", WAITING: "Waiting on Customer",
                RESOLVED: "Resolved", CLOSED: "Closed", CANCELLED: "Cancelled"}
STATUS_RULE = "status IN ('new', 'open', 'in_progress', 'waiting', 'resolved', 'closed', 'cancelled')"

# Priorities, lowest first. Customers choose up to High; only staff set Urgent.
LOW, NORMAL, HIGH, URGENT = "low", "normal", "high", "urgent"
PRIORITIES = (LOW, NORMAL, HIGH, URGENT)
CUSTOMER_PRIORITIES = (LOW, NORMAL, HIGH)
PRIORITY_RANK = {priority: number for number, priority in enumerate(PRIORITIES)}
PRIORITY_NAMES = {LOW: "Low", NORMAL: "Normal", HIGH: "High", URGENT: "Urgent"}
PRIORITY_RULE = "priority IN ('low', 'normal', 'high', 'urgent')"

TICKET_PREFIX = "TT"


class Desk(Base):
    """A support desk: its people, categories and tickets."""
    __tablename__ = "desks"
    # AUTOINCREMENT: SQLite would otherwise give a deleted demo desk's id to the next
    # one, and an old cookie could then open someone else's demo.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    demo_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # The number the next ticket gets (TT-1, TT-2, ...). Numbers are never reused.
    next_number: Mapped[int] = mapped_column(Integer, default=1)
    # The seq of the newest history line (audit.py). Raising it with an UPDATE
    # locks this row, so history lines are written one at a time per desk.
    audit_seq: Mapped[int] = mapped_column(Integer, default=0)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("desk_id", "email"),
        CheckConstraint(ROLE_RULE, name="role"),
        {"sqlite_autoincrement": True},  # see Desk: ids are never handed out twice
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    desk_id: Mapped[int] = mapped_column(ForeignKey("desks.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254))
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(10), default=CUSTOMER)
    password_hash: Mapped[str] = mapped_column(String(255))
    # There is no email sending needed to run Ticket Tock, so a forgotten password
    # is reset with this code. It is shown once and stored only as a hash.
    recovery_hash: Mapped[str | None] = mapped_column(String(255))
    # Adding 1 logs this user out everywhere (load_user() compares it).
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    # A staff member who left is switched off, never deleted: their name stays on the history.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    desk: Mapped[Desk] = relationship()

    @property
    def is_staff(self):
        return self.role in STAFF_ROLES

    @property
    def is_demo(self):
        return self.desk.is_demo

    @property
    def role_name(self):
        return ROLE_NAMES[self.role]

    @property
    def initial(self):
        return self.name[:1].upper()

    @property
    def first_name(self):
        return self.name.split()[0] if self.name.split() else self.name

    def __repr__(self):
        return f"<User {self.email} {self.role}>"


class Invite(Base):
    """A one-time link that makes a staff account with this role (invites.py)."""
    __tablename__ = "invites"
    __table_args__ = (CheckConstraint("role IN ('agent', 'lead', 'admin')", name="role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    desk_id: Mapped[int] = mapped_column(ForeignKey("desks.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))
    # Only a hash: someone who reads the database still can't use the link.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    used_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    used_by: Mapped[User | None] = relationship(foreign_keys=[used_by_id])

    @property
    def role_name(self):
        return ROLE_NAMES[self.role]


class Category(Base):
    """What a ticket is about. Customers pick one; the admin keeps the list."""
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("desk_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    desk_id: Mapped[int] = mapped_column(ForeignKey("desks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    # A category nobody should pick any more is hidden, not deleted: old tickets keep it.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Ticket(Base):
    """One issue from one customer. Its status only changes in workflow.py."""
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("desk_id", "number"),
        CheckConstraint(STATUS_RULE, name="status"),
        CheckConstraint(PRIORITY_RULE, name="priority"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    desk_id: Mapped[int] = mapped_column(ForeignKey("desks.id", ondelete="CASCADE"), index=True)
    # TT-12: the number people use. The id is only for the database.
    number: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(12), default=NEW, index=True)
    priority: Mapped[str] = mapped_column(String(8), default=NORMAL)
    # How the problem was solved, written when the ticket is resolved.
    resolution: Mapped[str] = mapped_column(Text, default="")
    # Optimistic locking: every change says "WHERE version = <the one I read>" and
    # adds 1, so two people changing the ticket at once can't overwrite each other.
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # When the team first answered in public (for response times).
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)

    customer: Mapped[User] = relationship(foreign_keys=[customer_id])
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])
    category: Mapped[Category | None] = relationship()
    messages: Mapped[list["Message"]] = relationship(back_populates="ticket", order_by="Message.id",
                                                     cascade="all, delete-orphan", passive_deletes=True)

    @property
    def key(self):
        return f"{TICKET_PREFIX}-{self.number}"

    @property
    def status_name(self):
        return STATUS_NAMES[self.status]

    @property
    def priority_name(self):
        return PRIORITY_NAMES[self.priority]

    @property
    def is_active(self):
        return self.status in ACTIVE

    @property
    def is_finished(self):
        return self.status in FINISHED


class Message(Base):
    """One reply in a ticket's conversation. Internal notes are for the team only."""
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    # Never shown to the customer (tickets.messages_for() leaves them out).
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="messages")
    author: Mapped[User | None] = relationship()


class AuditEvent(Base):
    """One line of history. Written by audit.log() and NEVER changed or deleted
    (only a whole demo desk goes, with everything in it).

    Each row stores a hash of its own contents plus the hash of the row before
    it (seq - 1, same desk). Changing or removing an old row breaks every hash
    after it, and audit.verify() finds the first broken one. UNIQUE(desk_id, seq)
    means two writers can't both add "row 41": the second one fails and retries.
    """
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("desk_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    desk_id: Mapped[int] = mapped_column(ForeignKey("desks.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    # Who did it. The name and role are copied, so the line still reads right if
    # the person is renamed or changes role later. None = Ticket Tock itself.
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[str] = mapped_column(String(80))
    actor_role: Mapped[str] = mapped_column(String(10), default="")
    verb: Mapped[str] = mapped_column(String(30))
    # For a change: which field, and its value before and after.
    field: Mapped[str] = mapped_column(String(30), default="")
    old_value: Mapped[str] = mapped_column(String(200), default="")
    new_value: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(String(300), default="")
    # The team sees every line; customers never see internal ones.
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))

    ticket: Mapped[Ticket | None] = relationship()
    actor: Mapped[User | None] = relationship()
