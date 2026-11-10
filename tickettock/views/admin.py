"""The Admin's pages: Staff (people, roles, invite links) and Categories.

Every view asks permissions.py first. A page someone may not open is 403:
everyone knows the Admin pages exist, so there is nothing to hide with a 404.
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import db, desks, invites
from .. import permissions as can
from ..auth import login_required, not_for_demo, require
from ..dates import utcnow
from ..models import A_ROLE, ROLE_NAMES, STAFF_ROLES, Category, Invite, User

bp = Blueprint("admin", __name__, url_prefix="/admin")

CATEGORY_NAME_MAX = 60
CATEGORY_DESCRIPTION_MAX = 200


def _staff(s):
    return s.scalars(select(User).where(User.desk_id == g.desk.id, User.role.in_(STAFF_ROLES))
                     .order_by(User.is_active.desc(), User.name)).all()


def _person(s, user_id):
    person = desks.get_in_desk(s, User, user_id, g.desk)
    if not person.is_staff:
        abort(404)
    return person


# ---------- Staff ----------

@bp.get("/staff")
@login_required
def staff():
    require(can.can_manage_staff(g.user), "Only an Admin can manage the staff.")
    s = db.session()
    customers = s.scalar(select(func.count()).select_from(User)
                         .where(User.desk_id == g.desk.id, User.role.not_in(STAFF_ROLES)))
    return render_template("admin/staff.html", staff=_staff(s), customers=customers,
                           invites=invites.waiting(s, g.desk, utcnow()), roles=can.roles_you_can_give(g.user),
                           role_names=ROLE_NAMES, new_link=request.args.get("link"))


@bp.post("/staff/invite")
@login_required
def invite():
    require(can.can_manage_staff(g.user), "Only an Admin can invite staff.")
    not_for_demo("The demo can't make invite links. Sign up to run your own desk.")
    role = request.form.get("role", "")
    if role not in can.roles_you_can_give(g.user):
        abort(400, "Choose a role from the list.")
    s = db.session()
    token = invites.create(s, g.desk, role, g.user, utcnow())
    s.commit()
    flash(f"Made an invite link for {A_ROLE[role]}. Copy it now: it is shown only once.")
    return redirect(url_for("admin.staff", link=url_for("auth.join", token=token, _external=True)))


@bp.post("/staff/invites/<int:invite_id>/cancel")
@login_required
def cancel_invite(invite_id):
    require(can.can_manage_staff(g.user), "Only an Admin can cancel invite links.")
    s = db.session()
    invite_row = desks.get_in_desk(s, Invite, invite_id, g.desk)
    invites.cancel(s, invite_row)
    s.commit()
    flash("The invite link no longer works.")
    return redirect(url_for("admin.staff"))


@bp.post("/staff/<int:user_id>/role")
@login_required
def change_role(user_id):
    s = db.session()
    person = _person(s, user_id)
    require(can.can_change_role(g.user, person), "You can't change this person's role.")
    role = request.form.get("role", "")
    if role not in can.roles_you_can_give(g.user):
        abort(400, "Choose a role from the list.")
    if role != person.role:
        person.role = role
        person.session_version += 1  # their open pages reload with the new role's menus
        s.commit()
        flash(f"{person.name} is now {A_ROLE[role]}.")
    return redirect(url_for("admin.staff"))


@bp.post("/staff/<int:user_id>/active")
@login_required
def switch(user_id):
    s = db.session()
    person = _person(s, user_id)
    require(can.can_switch_off(g.user, person), "You can't switch this account off.")
    person.is_active = request.form.get("active") == "1"
    person.session_version += 1  # logged out straight away
    s.commit()
    flash(f"{person.name} can log in again." if person.is_active
          else f"{person.name} can no longer log in. Their tickets and history stay.")
    return redirect(url_for("admin.staff"))


# ---------- Categories ----------

@bp.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    require(can.can_manage_categories(g.user), "Only an Admin can change the categories.")
    s = db.session()
    errors = {}
    name = description = ""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        errors = _category_problems(name, description)
        if not errors:
            last = s.scalar(select(func.max(Category.position)).where(Category.desk_id == g.desk.id))
            s.add(Category(desk_id=g.desk.id, name=name, description=description, position=(last or 0) + 1))
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                errors["name"] = "There is already a category with that name."
            else:
                flash(f"Added the category {name}.")
                return redirect(url_for("admin.categories"))
    return render_template("admin/categories.html", categories=desks.categories(s, g.desk, active_only=False),
                           errors=errors, name=name, description=description), 400 if errors else 200


def _category_problems(name, description):
    errors = {}
    if not name or len(name) > CATEGORY_NAME_MAX:
        errors["name"] = f"Use 1 to {CATEGORY_NAME_MAX} characters."
    if len(description) > CATEGORY_DESCRIPTION_MAX:
        errors["description"] = f"Use at most {CATEGORY_DESCRIPTION_MAX} characters."
    return errors


@bp.post("/categories/<int:category_id>")
@login_required
def edit_category(category_id):
    require(can.can_manage_categories(g.user), "Only an Admin can change the categories.")
    s = db.session()
    category = desks.get_in_desk(s, Category, category_id, g.desk)
    action = request.form.get("action")
    if action in ("up", "down"):
        ordered = desks.categories(s, g.desk, active_only=False)
        index = ordered.index(category)
        other = index - 1 if action == "up" else index + 1
        if 0 <= other < len(ordered):
            ordered[index], ordered[other] = ordered[other], ordered[index]
            for position, row in enumerate(ordered):
                row.position = position
    elif action in ("hide", "show"):
        category.is_active = action == "show"
        flash(f"{category.name} is {'shown to customers again' if category.is_active else 'hidden from customers'}.")
    else:
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        errors = _category_problems(name, description)
        if errors:
            abort(400, next(iter(errors.values())))
        category.name, category.description = name, description
        flash(f"Saved {name}.")
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        abort(409, "There is already a category with that name.")
    return redirect(url_for("admin.categories"))
