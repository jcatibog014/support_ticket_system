"""The history: one AuditEvent row for everything that happens, and a hash chain to prove it.

Append-only. This module only ever INSERTs. Nothing in Ticket Tock updates or
deletes a history line (tests/test_audit.py checks the code for it), so a
ticket's history is a true record of who did what, and when.

Tamper-evident. Each line stores
    hash = sha256(prev_hash + the line's own contents)
where prev_hash is the hash of the line before it (seq - 1, same desk). The
first line's prev_hash is 64 zeros. If someone edits an old line in the
database (or deletes one), its hash no longer matches, and neither does any
line after it. verify() walks the chain and names the first broken line.

One at a time. log() first raises desks.audit_seq with an UPDATE. That locks
the desk's row until the transaction ends (in PostgreSQL and in SQLite), so two
requests can't both read "the last line is 40" and both write line 41.
UNIQUE(desk_id, seq) is the second guard.

The templates turn a line into a sentence (_history.html), so the words can
change without touching the stored history.
"""
import hashlib
import json

from sqlalchemy import select, update

from .models import AuditEvent, Desk

GENESIS = "0" * 64
SYSTEM_NAME = "Ticket Tock"

# Verbs. A ticket's:
CREATED, STATUS, ASSIGNED, PRIORITY, CATEGORY = "created", "status", "assigned", "priority", "category"
REPLIED, NOTED = "replied", "noted"
# The desk's (no ticket):
ROLE_CHANGED, SWITCHED_OFF, SWITCHED_ON = "role_changed", "switched_off", "switched_on"
INVITE_MADE, INVITE_CANCELLED, JOINED = "invite_made", "invite_cancelled", "joined"
CATEGORY_ADDED, CATEGORY_CHANGED = "category_added", "category_changed"

LIMITS = {"actor_name": 80, "verb": 30, "field": 30, "old_value": 200, "new_value": 200, "note": 300}


def _contents(event):
    """Everything the hash covers, in a fixed order."""
    return json.dumps([
        event.desk_id, event.seq, event.ticket_id, event.actor_id, event.actor_name, event.actor_role,
        event.verb, event.field, event.old_value, event.new_value, event.note, bool(event.is_internal),
        event.created_at.isoformat(timespec="microseconds"),
    ], separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev_hash, event):
    return hashlib.sha256((prev_hash + _contents(event)).encode("utf-8")).hexdigest()


def _text(value, limit):
    value = "" if value is None else str(value)
    return value if len(value) <= limit else value[:limit - 1] + "…"


def log(s, desk_id, actor, verb, now, ticket=None, field="", old="", new="", note="", internal=False):
    """Write one history line (the caller commits). actor None = Ticket Tock itself."""
    s.flush()  # the ticket must have its id before we point at it
    s.execute(update(Desk).where(Desk.id == desk_id).values(audit_seq=Desk.audit_seq + 1))
    seq = s.scalar(select(Desk.audit_seq).where(Desk.id == desk_id))
    prev_hash = GENESIS if seq == 1 else s.scalar(
        select(AuditEvent.hash).where(AuditEvent.desk_id == desk_id, AuditEvent.seq == seq - 1))
    event = AuditEvent(
        desk_id=desk_id, seq=seq, ticket_id=ticket.id if ticket is not None else None,
        actor_id=actor.id if actor is not None else None,
        actor_name=_text(actor.name if actor is not None else SYSTEM_NAME, LIMITS["actor_name"]),
        actor_role=actor.role if actor is not None else "",
        verb=verb, field=_text(field, LIMITS["field"]), old_value=_text(old, LIMITS["old_value"]),
        new_value=_text(new, LIMITS["new_value"]), note=_text(note, LIMITS["note"]),
        is_internal=bool(internal), created_at=now.replace(tzinfo=None), prev_hash=prev_hash or GENESIS,
    )
    event.hash = compute_hash(event.prev_hash, event)
    s.add(event)
    return event


def verify(s, desk):
    """Walk the desk's whole chain. Returns (ok, lines checked, problem or None)."""
    expected_prev = GENESIS
    count = 0
    for event in s.scalars(select(AuditEvent).where(AuditEvent.desk_id == desk.id).order_by(AuditEvent.seq)):
        count += 1
        if event.seq != count:
            return False, count, f"Line {count} is missing."
        if event.prev_hash != expected_prev:
            return False, count, f"Line {event.seq} doesn't follow line {event.seq - 1}."
        if compute_hash(event.prev_hash, event) != event.hash:
            return False, count, f"Line {event.seq} was changed after it was written."
        expected_prev = event.hash
    newest = s.scalar(select(Desk.audit_seq).where(Desk.id == desk.id))
    if count != newest:
        return False, count, f"The newest {newest - count} line(s) are missing."
    return True, count, None
