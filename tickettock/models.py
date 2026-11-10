"""The data model: every table, as Python classes (SQLAlchemy 2.0).

    desks ──< users             (customers sign up; staff join with an invite)
      ├──< invites              (one-time links that add a staff member with a role)
      └──< categories           ("Billing", "Login and account", ...)

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
