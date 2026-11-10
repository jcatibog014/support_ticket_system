"""The Admin's pages: Staff (people, roles, invite links), Categories, and Time Limits
(working hours, the SLA limits per priority, holidays).

Every view asks permissions.py first. A page someone may not open is 403:
everyone knows the Admin pages exist, so there is nothing to hide with a 404.
"""
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import audit, db, desks, hours, invites, sla
from .. import permissions as can
from ..auth import login_required, not_for_demo, require
from ..dates import today, utcnow
from ..models import (A_ROLE, PRIORITIES, PRIORITY_NAMES, ROLE_NAMES, STAFF_ROLES, Category, Holiday, Invite, SlaRule,
                      User)

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
    now = utcnow()
    token = invites.create(s, g.desk, role, g.user, now)
    audit.log(s, g.desk.id, g.user, audit.INVITE_MADE, now, field="role", new=role)
    s.commit()
    flash(f"Made an invite link for {A_ROLE[role]}. Copy it now: it is shown only once.")
    return redirect(url_for("admin.staff", link=url_for("auth.join", token=token, _external=True)))


@bp.post("/staff/invites/<int:invite_id>/cancel")
@login_required
def cancel_invite(invite_id):
    require(can.can_manage_staff(g.user), "Only an Admin can cancel invite links.")
    s = db.session()
    invite_row = desks.get_in_desk(s, Invite, invite_id, g.desk)
    audit.log(s, g.desk.id, g.user, audit.INVITE_CANCELLED, utcnow(), field="role", old=invite_row.role)
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
        audit.log(s, g.desk.id, g.user, audit.ROLE_CHANGED, utcnow(), field="role", old=person.role, new=role,
                  note=f"{person.name} ({person.email})")
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
    active = request.form.get("active") == "1"
    if active != person.is_active:
        audit.log(s, g.desk.id, g.user, audit.SWITCHED_ON if active else audit.SWITCHED_OFF, utcnow(),
                  note=f"{person.name} ({person.email})")
    person.is_active = active
    person.session_version += 1  # logged out straight away
    s.commit()
    flash(f"{person.name} is active again and can log in." if person.is_active
          else f"{person.name} is deactivated and can't log in. Their tickets and history stay.")
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
                audit.log(s, g.desk.id, g.user, audit.CATEGORY_ADDED, utcnow(), new=name)  # flushes: may raise
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
    elif action in ("auto_on", "auto_off"):
        turn_on = action == "auto_on"
        if category.auto_assign != turn_on:
            audit.log(s, g.desk.id, g.user, audit.CATEGORY_CHANGED, utcnow(), field="auto_assign",
                      old="yes" if category.auto_assign else "no", new="yes" if turn_on else "no", note=category.name)
        category.auto_assign = turn_on
        flash(f"New {category.name} tickets now go to the Agents in turn." if turn_on
              else f"New {category.name} tickets now wait in the queue.")
    elif action in ("hide", "show"):
        if category.is_active != (action == "show"):
            audit.log(s, g.desk.id, g.user, audit.CATEGORY_CHANGED, utcnow(), field="shown",
                      old="yes" if category.is_active else "no", new="yes" if action == "show" else "no",
                      note=category.name)
        category.is_active = action == "show"
        flash(f"{category.name} is {'shown to customers again' if category.is_active else 'hidden from customers'}.")
    else:
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        errors = _category_problems(name, description)
        if errors:
            abort(400, next(iter(errors.values())))
        if name != category.name:
            audit.log(s, g.desk.id, g.user, audit.CATEGORY_CHANGED, utcnow(), field="name", old=category.name, new=name)
        category.name, category.description = name, description
        flash(f"Saved {name}.")
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        abort(409, "There is already a category with that name.")
    return redirect(url_for("admin.categories"))


# ---------- Working hours, time limits and holidays ----------

LIMIT_MAX_HOURS = 2000


def _hours_text(minutes):
    """Hours for a number box: 4, 0.5, 1.25."""
    text = f"{minutes / 60:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _limits_text(rule):
    return f"first reply {sla.short(rule[0])}, solved {sla.short(rule[1])}"


def _parse_time(text):
    """"08:30" -> 510, or None."""
    try:
        hour, minute = (int(part) for part in (text or "").split(":")[:2])
    except ValueError:
        return None
    return hour * 60 + minute if 0 <= hour < 24 and 0 <= minute < 60 else None


def _parse_hours(text):
    try:
        value = float((text or "").strip())
    except ValueError:
        return None
    return round(value * 60) if 0.25 <= value <= LIMIT_MAX_HOURS else None


@bp.route("/hours", methods=["GET", "POST"])
@login_required
def time_limits():
    require(can.can_manage_hours(g.user), "Only an Admin can change the working hours and time limits.")
    s = db.session()
    now = utcnow()
    errors = {}
    if request.method == "POST":
        not_for_demo("The demo can't change its time limits. Sign up to run your own desk.")
        action = request.form.get("action")
        if action == "hours":
            opens, closes = _parse_time(request.form.get("open")), _parse_time(request.form.get("close"))
            days = "".join(sorted({d for d in request.form.getlist("workdays") if d in "0123456" and len(d) == 1}))
            if opens is None or closes is None or opens >= closes:
                errors["hours"] = "The desk must close after it opens."
            elif not days:
                errors["hours"] = "Choose at least one working day."
            else:
                before = hours.describe(g.desk)
                g.desk.open_minute, g.desk.close_minute, g.desk.workdays = opens, closes, days
                if hours.describe(g.desk) != before:
                    audit.log(s, g.desk.id, g.user, audit.HOURS_CHANGED, now, field="hours", old=before,
                              new=hours.describe(g.desk))
                s.commit()
                flash("Working hours saved. New tickets use them now.")
                return redirect(url_for("admin.time_limits"))
        elif action == "limits":
            rules = {rule.priority: rule for rule in s.scalars(select(SlaRule).where(SlaRule.desk_id == g.desk.id))}
            new = {}
            for priority in PRIORITIES:
                response = _parse_hours(request.form.get(f"{priority}_response"))
                resolve = _parse_hours(request.form.get(f"{priority}_resolve"))
                if response is None or resolve is None:
                    errors["limits"] = f"Use hours from 0.25 to {LIMIT_MAX_HOURS}."
                elif response > resolve:
                    errors["limits"] = f"{PRIORITY_NAMES[priority]}: the first reply can't take longer than solving it."
                new[priority] = (response, resolve)
            if not errors:
                old = sla.rules_for(s, g.desk)
                for priority, (response, resolve) in new.items():
                    if old[priority] != (response, resolve):
                        audit.log(s, g.desk.id, g.user, audit.SLA_CHANGED, now, field=priority,
                                  old=_limits_text(old[priority]), new=_limits_text((response, resolve)))
                    rule = rules.get(priority) or SlaRule(desk_id=g.desk.id, priority=priority)
                    rule.response_minutes, rule.resolve_minutes = response, resolve
                    s.add(rule)
                s.commit()
                flash("Time limits saved. They apply to new tickets, and when a ticket's priority changes.")
                return redirect(url_for("admin.time_limits"))
        elif action == "holiday":
            name = request.form.get("name", "").strip()[:80]
            try:
                day = date.fromisoformat(request.form.get("day", ""))
            except ValueError:
                day = None
            if day is None or not name:
                errors["holiday"] = "Choose a day and give the holiday a name."
            else:
                s.add(Holiday(desk_id=g.desk.id, day=day, name=name))
                try:
                    audit.log(s, g.desk.id, g.user, audit.HOLIDAY_ADDED, now, new=day.isoformat(), note=name)
                    s.commit()
                except IntegrityError:
                    s.rollback()
                    errors["holiday"] = "That day is already a holiday."
                else:
                    flash(f"Added {name}.")
                    return redirect(url_for("admin.time_limits"))
        elif action == "ph_holidays":
            year = request.form.get("year", type=int)
            if year not in (today().year, today().year + 1):
                abort(400, "Choose this year or next year.")
            added = hours.add_ph_holidays(s, g.desk, year)
            for holiday in added:
                audit.log(s, g.desk.id, g.user, audit.HOLIDAY_ADDED, now, new=holiday.day.isoformat(), note=holiday.name)
            s.commit()
            flash(f"Added {len(added)} Philippine holidays for {year}." if added
                  else f"The {year} holidays are already there.")
            return redirect(url_for("admin.time_limits"))
    rules = sla.rules_for(s, g.desk)
    holidays = s.scalars(select(Holiday).where(Holiday.desk_id == g.desk.id, Holiday.day >= date(today().year, 1, 1))
                         .order_by(Holiday.day)).all()
    return render_template("admin/hours.html", errors=errors, rules=rules, holidays=holidays, today=today(),
                           hours_text=_hours_text, weekdays=hours.WEEKDAY_NAMES, clock=hours.clock,
                           described=hours.describe(g.desk), priorities=PRIORITIES,
                           day_length=_hours_text(g.desk.close_minute - g.desk.open_minute) + " hours"), 400 if errors else 200


@bp.post("/holidays/<int:holiday_id>/remove")
@login_required
def remove_holiday(holiday_id):
    require(can.can_manage_hours(g.user), "Only an Admin can change the holidays.")
    not_for_demo("The demo can't change its holidays. Sign up to run your own desk.")
    s = db.session()
    holiday = desks.get_in_desk(s, Holiday, holiday_id, g.desk)
    audit.log(s, g.desk.id, g.user, audit.HOLIDAY_REMOVED, utcnow(), old=holiday.day.isoformat(), note=holiday.name)
    s.delete(holiday)
    s.commit()
    flash(f"Removed {holiday.name}.")
    return redirect(url_for("admin.time_limits"))
