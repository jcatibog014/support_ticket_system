"""The history: append-only lines in a hash chain that shows any tampering."""
import re
from pathlib import Path

from sqlalchemy import select, text, update

from tickettock import audit, db, desks
from tickettock.dates import utcnow
from tickettock.models import AuditEvent, User

from .conftest import make_team, make_ticket

PACKAGE = Path(__file__).parent.parent / "tickettock"


def chain(app):
    with app.app_context():
        s = db.session()
        return audit.verify(s, desks.main_desk(s))


def test_every_line_points_at_the_one_before(app):
    team = make_team(app)
    for _ in range(3):
        make_ticket(app, team["customer"])
    with app.app_context():
        rows = db.session().scalars(select(AuditEvent).order_by(AuditEvent.seq)).all()
        assert [row.seq for row in rows] == [1, 2, 3]
        assert rows[0].prev_hash == audit.GENESIS
        assert rows[1].prev_hash == rows[0].hash and rows[2].prev_hash == rows[1].hash
    assert chain(app) == (True, 3, None)


def test_changing_an_old_line_is_found(app):
    team = make_team(app)
    for _ in range(3):
        make_ticket(app, team["customer"])
    with app.app_context():
        s = db.session()
        s.execute(update(AuditEvent).where(AuditEvent.seq == 2).values(actor_name="Someone else"))
        s.commit()
    assert chain(app) == (False, 2, "Line 2 was changed after it was written.")


def test_deleting_a_line_is_found(app):
    team = make_team(app)
    for _ in range(3):
        make_ticket(app, team["customer"])
    with app.app_context():
        s = db.session()
        s.execute(text("DELETE FROM audit_events WHERE seq = 3"))
        s.commit()
    ok, _, problem = chain(app)
    assert not ok and "missing" in problem


def test_rewriting_a_line_and_its_hash_still_breaks_the_next_one(app):
    team = make_team(app)
    for _ in range(3):
        make_ticket(app, team["customer"])
    with app.app_context():
        s = db.session()
        row = s.scalar(select(AuditEvent).where(AuditEvent.seq == 2))
        row.new_value = "A nicer subject"
        row.hash = audit.compute_hash(row.prev_hash, row)  # a careful forger
        s.commit()
    assert chain(app) == (False, 3, "Line 3 doesn't follow line 2.")


def test_each_desk_has_its_own_chain(app):
    team = make_team(app)
    make_ticket(app, team["customer"])
    with app.app_context():
        s = db.session()
        other = desks.create_desk(s, "Demo desk", is_demo=True)
        actor = s.get(User, team["agent"])
        audit.log(s, other.id, actor, audit.CREATED, utcnow())
        s.commit()
        row = s.scalar(select(AuditEvent).where(AuditEvent.desk_id == other.id))
        assert row.seq == 1 and row.prev_hash == audit.GENESIS
        assert audit.verify(s, other) == (True, 1, None)


def test_long_values_are_shortened_not_refused(app):
    team = make_team(app)
    with app.app_context():
        s = db.session()
        event = audit.log(s, desks.main_desk(s).id, s.get(User, team["agent"]), audit.CREATED, utcnow(),
                          note="x" * 1000)
        s.commit()
        assert len(event.note) == 300
    assert chain(app)[0]


def test_no_code_ever_changes_or_deletes_a_history_line():
    """Append-only is a promise about the code, so check the code."""
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"(update|delete)\(\s*AuditEvent", source) or re.search(r"(UPDATE|DELETE FROM)\s+audit_events", source):
            offenders.append(path.name)
    assert offenders == []
