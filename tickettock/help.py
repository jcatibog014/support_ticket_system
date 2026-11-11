"""Help Articles: finding the right one for what a customer typed.

The search is simple on purpose: split the words, drop the tiny ones, and
score each published article (a word in the title counts 3, in the text 1).
A help desk has tens of articles, not millions, so Python can read them all.
"""
import re

from sqlalchemy import func, select

from . import permissions as can
from .models import Article, ArticleVote

TITLE_MAX, BODY_MAX = 120, 20000
STOP_WORDS = {"the", "and", "for", "you", "your", "are", "can", "cant", "not", "with", "this", "that", "have", "how",
              "what", "when", "why", "my", "me", "it", "is", "to", "do", "of", "in", "on", "a", "an", "i", "im"}


def words(text):
    return {word for word in re.findall(r"[a-z0-9]+", (text or "").lower().replace("'", ""))
            if len(word) > 2 and word not in STOP_WORDS}


def visible(s, user, desk):
    """Published articles, plus drafts for the team."""
    query = select(Article).where(Article.desk_id == desk.id)
    if not can.is_staff(user):
        query = query.where(Article.is_published.is_(True))
    return s.scalars(query.order_by(Article.title)).all()


def search(s, user, desk, text, limit=None):
    """The articles that match the words, best first."""
    wanted = words(text)
    if not wanted:
        return []
    scored = []
    for article in visible(s, user, desk):
        title, body = words(article.title), words(article.body)
        score = 3 * len(wanted & title) + len(wanted & body)
        if score:
            scored.append((-score, article.title, article))
    scored.sort(key=lambda row: row[:2])
    found = [article for _, _, article in scored]
    return found[:limit] if limit else found


def suggest(s, user, desk, text):
    """Up to 3 PUBLISHED articles for the New Ticket page (the team's drafts never show there)."""
    return [article for article in search(s, user, desk, text) if article.is_published][:3]


def problems(title, body):
    errors = {}
    if not title or len(title) > TITLE_MAX:
        errors["title"] = f"Use 1 to {TITLE_MAX} characters."
    if not body or len(body) > BODY_MAX:
        errors["body"] = f"Write the article (at most {BODY_MAX} characters)."
    return errors


def votes(s, article):
    """(helpful, not helpful)."""
    rows = dict(s.execute(select(ArticleVote.helpful, func.count()).where(ArticleVote.article_id == article.id)
                          .group_by(ArticleVote.helpful)).all())
    return rows.get(True, 0), rows.get(False, 0)


def my_vote(s, article, user):
    if user is None:
        return None
    return s.scalar(select(ArticleVote.helpful).where(ArticleVote.article_id == article.id,
                                                      ArticleVote.user_id == user.id))


def more_like(s, user, desk, article, limit=5):
    """Other published articles for the side of an article: (articles, all from its category?).
    The same category first; when it has no others, any other articles."""
    others = [other for other in visible(s, user, desk) if other.id != article.id and other.is_published]
    same = [other for other in others if article.category_id and other.category_id == article.category_id]
    return (same[:limit], True) if same else (others[:limit], False)
