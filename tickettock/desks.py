"""The desk a page is about.

There is one real desk, made by "flask create-admin". Every Try Demo makes
its own small desk, so a visitor's changes never reach the real one. Every
query for tickets, categories or people starts from a desk, and a row from
another desk gives 404.
"""
from flask import abort
from sqlalchemy import select

from .models import Category, Desk

MAIN_DESK_NAME = "Ticket Tock Support"

STARTER_CATEGORIES = (
    ("Login and account", "Can't log in, password, profile."),
    ("Billing", "Payments, invoices, refunds."),
    ("Bug report", "Something on the site doesn't work."),
    ("Question", "How do I...?"),
)


def main_desk(s):
    """The real desk (the oldest one that isn't a demo), or None before it is made."""
    return s.scalar(select(Desk).where(Desk.is_demo.is_(False)).order_by(Desk.id).limit(1))


def create_desk(s, name=MAIN_DESK_NAME, **fields):
    desk = Desk(name=name, **fields)
    s.add(desk)
    s.flush()
    for position, (category, description) in enumerate(STARTER_CATEGORIES):
        s.add(Category(desk_id=desk.id, name=category, description=description, position=position))
    from . import sla
    sla.make_default_rules(s, desk)
    return desk


def get_in_desk(s, model, row_id, desk):
    """A row by id, but only from this desk: anything else is "not found"."""
    row = s.get(model, row_id) if isinstance(row_id, int) else None
    if row is None or desk is None or row.desk_id != desk.id:
        abort(404)
    return row


def categories(s, desk, active_only=True):
    query = select(Category).where(Category.desk_id == desk.id)
    if active_only:
        query = query.where(Category.is_active.is_(True))
    return s.scalars(query.order_by(Category.position, Category.name)).all()
