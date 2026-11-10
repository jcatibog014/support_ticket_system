"""The Insights page (Team Leads and Admins)."""
from flask import Blueprint, g, render_template, request

from .. import db, hours, insights
from .. import permissions as can
from ..auth import login_required, require
from ..dates import utcnow

bp = Blueprint("insights", __name__)


@bp.get("/insights")
@login_required
def index():
    require(can.can_view_insights(g.user), "Insights are for Team Leads and Admins.")
    s = db.session()
    now = utcnow()
    days = request.args.get("days", 30, type=int)
    if days not in insights.PERIODS:
        days = 30
    since, first_day = insights.start_of(days, now)
    rows = insights.tickets_since(s, g.desk, since)
    holidays = hours.holiday_days(s, g.desk)
    reply_rate, solved_rate = insights.sla_rates(rows, since, now)
    trend, step = insights.sent_and_solved(rows, first_day, days)
    stars, average, rated = insights.ratings(rows, since)
    return render_template(
        "insights.html", days=days, periods=insights.PERIODS, statuses=insights.open_by_status(s, g.desk),
        trend=trend, step=step, reply_rate=reply_rate, solved_rate=solved_rate,
        reply_rows=insights.time_rows(insights.median_times(rows, g.desk, holidays, since, "first_response_at")),
        solve_rows=insights.time_rows(insights.median_times(rows, g.desk, holidays, since, "resolved_at")),
        team=insights.team_load(s, g.desk, rows, since), stars=stars, average=average, rated=rated,
        sent=sum(row[2] for row in trend), solved=sum(row[3] for row in trend), axis=insights.axis)
