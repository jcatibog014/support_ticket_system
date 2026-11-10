"""One-time invite links: open one to make a staff account with the role it gives.

Only a hash of the token is stored, so someone who reads the database can't
use a link. SHA-256 is right here, unlike for passwords, because the token is
32 random bytes: there is nothing to guess, so it needs no slow hash.
"""
import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select, update

from .models import Invite

LIFETIME = timedelta(days=7)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create(s, desk, role, creator, now):
    """Returns the token. This is the only time it exists in the clear."""
    token = secrets.token_urlsafe(32)
    s.add(Invite(desk_id=desk.id, role=role, token_hash=_hash(token), created_by_id=creator.id,
                 created_at=now, expires_at=now + LIFETIME))
    return token


def find_usable(s, token, now):
    invite = s.scalar(select(Invite).where(Invite.token_hash == _hash(token or "")))
    if invite is None or invite.used_at is not None or invite.expires_at <= now:
        return None
    return invite


def claim(s, invite, user, now):
    """Mark it used, or return False if someone just used it. The WHERE makes this
    safe: of two people pressing at once, the database lets only one update it."""
    result = s.execute(update(Invite).where(Invite.id == invite.id, Invite.used_at.is_(None))
                       .values(used_at=now, used_by_id=user.id))
    return result.rowcount == 1


def waiting(s, desk, now):
    """Links nobody has used yet and that still work."""
    return s.scalars(select(Invite).where(Invite.desk_id == desk.id, Invite.used_at.is_(None),
                                          Invite.expires_at > now)
                     .order_by(Invite.created_at.desc())).all()


def cancel(s, invite):
    s.delete(invite)
