"""Stage 7: the Insights page and its numbers."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tickettock import insights
from tickettock.dates import utcnow

from .conftest import browser, make_team, make_ticket

NOW = datetime(2026, 11, 20, 4, 0)  # noon in the Philippines
ALWAYS_OPEN = SimpleNamespace(open_minute=0, close_minute=24 * 60, workdays="0123456")


@pytest.fixture
def team(app):
    return make_team(app)


def fake(**fields):
    base = dict(created_at=NOW - timedelta(days=1), first_response_at=None, resolved_at=None, rated_at=None,
                rating=None, response_due_at=None, resolve_due_at=None, status="open", paused_since=None,
                paused_minutes=0, priority="normal", assignee_id=None)
    return SimpleNamespace(**{**base, **fields})


@pytest.mark.parametrize("who, status", [("customer", 403), ("agent", 403), ("lead", 200), ("admin", 200)])
def test_who_opens_insights(app, team, who, status):
    assert browser(app, team[who]).get("/insights").status_code == status


@pytest.mark.parametrize("days", [7, 30, 90, 12345])
def test_every_period_draws(app, team, days):
    make_ticket(app, team["customer"], status="resolved", assignee_id=team["agent"], resolution="Done.",
                resolved_at=utcnow(), first_response_at=utcnow(), rating=4, rated_at=utcnow())
    text = browser(app, team["lead"]).get(f"/insights?days={days}").get_data(as_text=True)
    assert "Sent and Solved" in text and "Show as a table" in text
    assert "Ana Reyes" in text  # Team Load


def test_sla_rates_count_late_tickets_without_a_reply_as_missed():
    since = NOW - timedelta(days=7)
    rows = [
        fake(response_due_at=NOW - timedelta(hours=5), first_response_at=NOW - timedelta(hours=6)),  # met
        fake(response_due_at=NOW - timedelta(hours=5), first_response_at=NOW - timedelta(hours=4)),  # missed
        fake(response_due_at=NOW - timedelta(hours=1)),  # no reply, late: missed
        fake(response_due_at=NOW + timedelta(hours=1)),  # still has time: not counted
        fake(created_at=NOW - timedelta(days=30), response_due_at=NOW - timedelta(days=29)),  # before the period
    ]
    reply, _ = insights.sla_rates(rows, since, NOW)
    assert (reply.met, reply.missed, reply.percent) == (1, 2, 33)


def test_waiting_tickets_are_not_counted_late_for_solving():
    rows = [fake(status="waiting", paused_since=NOW - timedelta(hours=2), resolve_due_at=NOW - timedelta(hours=1))]
    _, solved = insights.sla_rates(rows, NOW - timedelta(days=7), NOW)
    assert solved.total == 0 and solved.percent is None


def test_median_times_by_priority_skip_waiting_time():
    rows = [
        fake(priority="urgent", created_at=NOW - timedelta(hours=3), resolved_at=NOW, paused_minutes=60),
        fake(priority="urgent", created_at=NOW - timedelta(hours=5), resolved_at=NOW),
        fake(priority="urgent", created_at=NOW - timedelta(hours=1), resolved_at=NOW),
    ]
    found = insights.median_times(rows, ALWAYS_OPEN, set(), NOW - timedelta(days=7), "resolved_at")
    assert found[0] == ("urgent", "Urgent", 120, 3)  # 60, 120 and 300 minutes: the middle is 120
    assert found[1] == ("high", "High", None, 0)


def test_sent_and_solved_per_day_and_per_week():
    since, first_day = insights.start_of(7, NOW)
    rows = [fake(created_at=NOW), fake(created_at=NOW - timedelta(days=2), resolved_at=NOW)]
    daily, step = insights.sent_and_solved(rows, first_day, 7)
    assert step == 1 and len(daily) == 7
    assert [(sent, solved) for _, _, sent, solved in daily][-3:] == [(1, 0), (0, 0), (1, 1)]
    _, first_day = insights.start_of(90, NOW)
    weekly, step = insights.sent_and_solved(rows, first_day, 90)
    assert step == 7 and sum(row[2] for row in weekly) == 2


def test_ratings():
    rows = [fake(rating=5, rated_at=NOW), fake(rating=4, rated_at=NOW), fake(rating=5, rated_at=NOW - timedelta(days=60))]
    stars, average, total = insights.ratings(rows, NOW - timedelta(days=7))
    assert dict(stars)[5] == 1 and dict(stars)[4] == 1 and (average, total) == (4.5, 2)


@pytest.mark.parametrize("value, top", [(0, 5), (4, 5), (6, 10), (13, 15), (47, 50), (130, 150)])
def test_nice_max(value, top):
    assert insights.nice_max(value) == top
