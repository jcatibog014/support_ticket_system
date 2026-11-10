"""Stage 4: working time, time limits (SLA), escalation, auto-close and the timer."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tickettock import db, hours, sla, tick, tickets, workflow
from tickettock.dates import from_local, to_local, utcnow
from tickettock.models import AuditEvent, Holiday, Notification, SlaRule, Ticket, User

from .conftest import browser, get_ticket, make_team, make_ticket, post

DESK = SimpleNamespace(open_minute=8 * 60, close_minute=17 * 60, workdays="01234")


def local(*args):
    """A Philippine time -> naive UTC, like the database keeps."""
    return from_local(datetime(*args))


def as_local(moment):
    return to_local(moment).replace(tzinfo=None)


@pytest.fixture
def team(app):
    return make_team(app)


# ---------- Working time ----------

def test_four_working_hours_from_friday_afternoon_is_monday_morning():
    friday_4pm = local(2026, 11, 13, 16, 0)
    assert as_local(hours.add_minutes(DESK, friday_4pm, 240, set())) == datetime(2026, 11, 16, 11, 0)


def test_the_clock_starts_at_opening_time():
    monday_6am = local(2026, 11, 16, 6, 0)
    assert as_local(hours.add_minutes(DESK, monday_6am, 60, set())) == datetime(2026, 11, 16, 9, 0)
    saturday = local(2026, 11, 14, 10, 0)
    assert as_local(hours.add_minutes(DESK, saturday, 30, set())) == datetime(2026, 11, 16, 8, 30)


def test_holidays_are_skipped():
    friday_4pm = local(2026, 11, 27, 16, 0)
    monday = date(2026, 11, 30)  # Bonifacio Day
    assert as_local(hours.add_minutes(DESK, friday_4pm, 120, {monday})) == datetime(2026, 12, 1, 9, 0)


def test_minutes_between_counts_only_working_time():
    assert hours.minutes_between(DESK, local(2026, 11, 13, 16, 0), local(2026, 11, 16, 9, 0), set()) == 120
    assert hours.minutes_between(DESK, local(2026, 11, 16, 9, 0), local(2026, 11, 16, 8, 0), set()) == 0


def test_add_and_between_agree():
    start = local(2026, 11, 12, 15, 37)
    for minutes in (1, 59, 540, 1000, 2700):
        end = hours.add_minutes(DESK, start, minutes, set())
        assert hours.minutes_between(DESK, start, end, set()) == minutes


def test_a_desk_that_never_opens_says_so():
    with pytest.raises(hours.NeverOpen):
        hours.add_minutes(SimpleNamespace(open_minute=480, close_minute=1020, workdays=""), local(2026, 1, 5, 9), 60, set())


def test_describe():
    assert hours.describe(DESK) == "Monday to Friday, 8:00 AM to 5:00 PM"
    assert hours.describe(SimpleNamespace(open_minute=570, close_minute=780, workdays="05")) == \
        "Monday, Saturday, 9:30 AM to 1:00 PM"


@pytest.mark.parametrize("year, good_friday", [(2026, date(2026, 4, 3)), (2027, date(2027, 3, 26))])
def test_holy_week_moves_with_easter(year, good_friday):
    assert (good_friday, "Good Friday") in hours.ph_holidays(year)


def test_heroes_day_is_the_last_monday_of_august():
    assert (date(2026, 8, 31), "National Heroes Day") in hours.ph_holidays(2026)


# ---------- The clocks on tickets ----------

def test_a_new_ticket_gets_its_due_times(app, team):
    monday_9am = local(2026, 11, 16, 9, 0)
    number = make_ticket(app, team["customer"], now=monday_9am)
    ticket = get_ticket(app, number)
    # Normal: first reply within 9 working hours (one working day), solved within 5 working days.
    assert as_local(ticket.response_due_at) == datetime(2026, 11, 17, 9, 0)
    assert as_local(ticket.resolve_due_at) == datetime(2026, 11, 23, 9, 0)


def test_a_priority_change_moves_the_due_times(app, team):
    monday_9am = local(2026, 11, 16, 9, 0)
    number = make_ticket(app, team["customer"], now=monday_9am)
    lead = browser(app, team["lead"])
    post(lead, f"/tickets/{number}/priority", {"priority": "urgent", "version": str(get_ticket(app, number).version)})
    ticket = get_ticket(app, number)
    assert as_local(ticket.response_due_at) == datetime(2026, 11, 16, 10, 0)
    assert as_local(ticket.resolve_due_at) == datetime(2026, 11, 16, 13, 0)


def test_waiting_on_the_customer_pauses_the_fix_clock(app, team):
    monday_9am = local(2026, 11, 16, 9, 0)
    number = make_ticket(app, team["customer"], now=monday_9am, status="in_progress", assignee_id=team["agent"])
    with app.app_context():
        s = db.session()
        ticket = s.scalar(select(Ticket).where(Ticket.number == number))
        agent = s.get(User, team["agent"])
        before = ticket.resolve_due_at
        workflow.move(s, ticket, "waiting", agent, local(2026, 11, 16, 10, 0))
        workflow.move(s, ticket, "in_progress", agent, local(2026, 11, 17, 10, 0))  # one working day later
        s.commit()
        assert ticket.paused_minutes == 540 and ticket.paused_since is None
        assert as_local(ticket.resolve_due_at) == as_local(before) + timedelta(days=1)  # one working day later too


CLOCK_DESK = SimpleNamespace(open_minute=0, close_minute=24 * 60, workdays="0123456")  # always open: easy sums
RULES = sla.DEFAULT_RULES


def fake(status="open", response_due=None, resolve_due=None, first_response_at=None, resolved_at=None, priority="urgent"):
    return SimpleNamespace(status=status, priority=priority, response_due_at=response_due, resolve_due_at=resolve_due,
                           first_response_at=first_response_at, resolved_at=resolved_at)


NOW = datetime(2026, 11, 16, 12, 0)


@pytest.mark.parametrize("ticket, state", [
    (fake(response_due=NOW + timedelta(minutes=50)), "ok"),
    (fake(response_due=NOW + timedelta(minutes=10)), "soon"),  # under 25% of the 60-minute limit
    (fake(response_due=NOW - timedelta(minutes=5)), "late"),
    (fake(response_due=NOW - timedelta(minutes=5), first_response_at=NOW - timedelta(minutes=30)), "met"),
    (fake(response_due=NOW - timedelta(minutes=30), first_response_at=NOW - timedelta(minutes=5)), "missed"),
])
def test_first_reply_clock_states(ticket, state):
    reply, _ = sla.clocks(ticket, CLOCK_DESK, NOW, RULES, set())
    assert reply.state == state


def test_the_fix_clock_is_paused_while_waiting():
    _, solved = sla.clocks(fake(status="waiting", resolve_due=NOW - timedelta(hours=1)), CLOCK_DESK, NOW, RULES, set())
    assert solved.state == "paused"


def test_the_headline_shows_the_first_reply_then_the_fix():
    waiting_reply = fake(response_due=NOW + timedelta(hours=1), resolve_due=NOW + timedelta(hours=4))
    assert sla.headline(waiting_reply, CLOCK_DESK, NOW, RULES, set()).kind == "response"
    answered = fake(response_due=NOW, resolve_due=NOW + timedelta(hours=4), first_response_at=NOW - timedelta(minutes=1))
    assert sla.headline(answered, CLOCK_DESK, NOW, RULES, set()).kind == "resolve"
    assert sla.headline(fake(status="closed"), CLOCK_DESK, NOW, RULES, set()) is None


def test_clock_words():
    assert sla.Clock("response", "soon", NOW, 45).label == "First reply due in 45 min"
    assert sla.Clock("resolve", "late", NOW, 200).label == "Solved 3 h 20 min late"


# ---------- Escalation and the timer ----------

def make_late(app, team, **fields):
    return make_ticket(app, team["customer"], now=utcnow() - timedelta(days=30), **fields)


def run_tick(app):
    with app.test_request_context():
        return tick.run(db.session(), utcnow())


def test_the_timer_escalates_a_late_ticket_once(app, team):
    number = make_late(app, team, status="open", assignee_id=team["agent"])
    assert run_tick(app)["escalated"] == 1
    assert run_tick(app)["escalated"] == 0
    ticket = get_ticket(app, number)
    assert ticket.escalated_at is not None and "first reply is late" in ticket.escalation_reason
    with app.app_context():
        s = db.session()
        told = set(s.scalars(select(Notification.user_id).where(Notification.kind == "escalated")))
        line = s.scalar(select(AuditEvent).where(AuditEvent.verb == "escalated"))
    assert told == {team["agent"], team["lead"], team["admin"]}  # not the customer, not other agents
    assert line.is_internal and line.actor_name == "Ticket Tock"


def test_waiting_tickets_are_not_escalated_for_the_fix(app, team):
    make_late(app, team, status="waiting", assignee_id=team["agent"], first_response_at=utcnow() - timedelta(days=29))
    assert run_tick(app)["escalated"] == 0


def test_the_overdue_tab(app, team):
    make_late(app, team, subject="Very late one")
    make_ticket(app, team["customer"], subject="Fresh one")
    text = browser(app, team["agent"]).get("/queue?tab=overdue").get_data(as_text=True)
    assert "Very late one" in text and "Fresh one" not in text
    assert "late" in text


def test_opening_the_queue_runs_the_timer(app, team):
    number = make_late(app, team)
    browser(app, team["agent"]).get("/queue")
    assert get_ticket(app, number).escalated_at is not None


def test_customers_never_see_escalations(app, team):
    number = make_late(app, team)
    run_tick(app)
    text = browser(app, team["customer"]).get(f"/tickets/{number}").get_data(as_text=True)
    assert "scalat" not in text and "Time Limits" not in text


def test_an_agent_asks_a_team_lead_for_help(app, team):
    number = make_ticket(app, team["customer"], status="in_progress", assignee_id=team["agent"])
    version = str(get_ticket(app, number).version)
    assert post(browser(app, team["other_agent"]), f"/tickets/{number}/escalate",
                {"reason": "Help", "version": version}).status_code == 403
    post(browser(app, team["agent"]), f"/tickets/{number}/escalate", {"reason": "I need billing access.", "version": version})
    ticket = get_ticket(app, number)
    assert ticket.escalation_reason == "I need billing access."
    assert post(browser(app, team["agent"]), f"/tickets/{number}/clear-escalation",
                {"version": str(ticket.version)}).status_code == 403
    post(browser(app, team["lead"]), f"/tickets/{number}/clear-escalation", {"version": str(ticket.version)})
    assert get_ticket(app, number).escalated_at is None


def test_resolved_tickets_close_by_themselves_after_seven_days(app, team):
    old = make_ticket(app, team["customer"], status="resolved", resolution="Done.", resolved_at=utcnow() - timedelta(days=8))
    recent = make_ticket(app, team["customer"], status="resolved", resolution="Done.", resolved_at=utcnow() - timedelta(days=2))
    assert run_tick(app)["closed"] == 1
    assert get_ticket(app, old).status == "closed" and get_ticket(app, recent).status == "resolved"
    with app.app_context():
        note = db.session().scalar(select(AuditEvent.note).where(AuditEvent.new_value == "closed"))
    assert note == "Closed by itself 7 days after it was resolved."


def test_the_timer_url_needs_its_key(app, team):
    client = app.test_client()
    assert client.post("/tasks/tick").status_code == 404  # no key set: it doesn't exist
    app.config["TICK_KEY"] = "secret-key"
    assert client.post("/tasks/tick", headers={"X-Tick-Key": "wrong"}).status_code == 404
    response = client.post("/tasks/tick", headers={"X-Tick-Key": "secret-key"})
    assert response.status_code == 200 and response.get_json()["escalated"] == 0


def test_tick_command(app, team):
    make_late(app, team)
    result = app.test_cli_runner().invoke(args=["tick"])
    assert result.exit_code == 0 and "escalated: 1" in result.output


# ---------- The Time Limits page ----------

@pytest.mark.parametrize("who, status", [("lead", 403), ("admin", 200)])
def test_only_admins_open_time_limits(app, team, who, status):
    assert browser(app, team[who]).get("/admin/hours").status_code == status


def test_saving_working_hours(app, team):
    admin = browser(app, team["admin"])
    post(admin, "/admin/hours", {"action": "hours", "open": "09:00", "close": "18:00", "workdays": ["0", "1", "2", "3", "4", "5"]})
    with app.app_context():
        desk = db.session().get(tickets.Desk, 1)
        assert (desk.open_minute, desk.close_minute, desk.workdays) == (540, 1080, "012345")
    assert "Changed the working hours to Monday to Saturday, 9:00 AM to 6:00 PM" in \
        admin.get("/audit?what=settings").get_data(as_text=True)
    bad = post(admin, "/admin/hours", {"action": "hours", "open": "18:00", "close": "09:00", "workdays": ["0"]})
    assert bad.status_code == 400


def test_saving_time_limits(app, team):
    admin = browser(app, team["admin"])
    form = {"action": "limits"}
    for priority in ("low", "normal", "high", "urgent"):
        form[f"{priority}_response"], form[f"{priority}_resolve"] = "2", "10"
    form["urgent_response"], form["urgent_resolve"] = "0.5", "2"
    post(admin, "/admin/hours", form)
    with app.app_context():
        rule = db.session().scalar(select(SlaRule).where(SlaRule.priority == "urgent"))
        assert (rule.response_minutes, rule.resolve_minutes) == (30, 120)
    bad = post(admin, "/admin/hours", {**form, "urgent_response": "5", "urgent_resolve": "1"})
    assert bad.status_code == 400 and "first reply can" in bad.get_data(as_text=True)


def test_holidays(app, team):
    admin = browser(app, team["admin"])
    post(admin, "/admin/hours", {"action": "holiday", "day": "2026-12-26", "name": "Office party"})
    assert post(admin, "/admin/hours", {"action": "holiday", "day": "2026-12-26", "name": "Again"}).status_code == 400
    year = to_local(utcnow()).year
    post(admin, "/admin/hours", {"action": "ph_holidays", "year": str(year)})
    with app.app_context():
        count = len(db.session().scalars(select(Holiday)).all())
    post(admin, "/admin/hours", {"action": "ph_holidays", "year": str(year)})  # again: nothing new
    with app.app_context():
        s = db.session()
        assert len(s.scalars(select(Holiday)).all()) == count
        party = s.scalar(select(Holiday).where(Holiday.name == "Office party"))
    post(admin, f"/admin/holidays/{party.id}/remove")
    with app.app_context():
        assert db.session().scalar(select(Holiday).where(Holiday.name == "Office party")) is None


# ---------- Escalations clear by themselves when their reason is gone ----------

def test_the_first_reply_clears_a_no_reply_escalation(app, team):
    number = make_late(app, team)
    run_tick(app)
    assert get_ticket(app, number).escalation_reason == sla.FIRST_REPLY_LATE
    post(browser(app, team["agent"]), f"/tickets/{number}/reply", {"body": "Sorry for the wait! Looking now."})
    assert get_ticket(app, number).escalated_at is None
    text = browser(app, team["lead"]).get(f"/tickets/{number}").get_data(as_text=True)
    assert "cleared the escalation (the first reply was sent)" in text


def test_assigning_alone_keeps_the_escalation(app, team):
    number = make_late(app, team)
    run_tick(app)
    post(browser(app, team["agent"]), f"/tickets/{number}/take", {"version": str(get_ticket(app, number).version)})
    assert get_ticket(app, number).escalated_at is not None  # the customer still has no answer


def test_resolving_clears_any_escalation(app, team):
    number = make_ticket(app, team["customer"], status="in_progress", assignee_id=team["agent"])
    agent = browser(app, team["agent"])
    post(agent, f"/tickets/{number}/escalate", {"reason": "Need billing access.", "version": str(get_ticket(app, number).version)})
    post(agent, f"/tickets/{number}/move", {"to": "resolved", "resolution": "Done.", "version": str(get_ticket(app, number).version)})
    ticket = get_ticket(app, number)
    assert ticket.status == "resolved" and ticket.escalated_at is None
