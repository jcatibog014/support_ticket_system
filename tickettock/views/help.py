"""Help Articles: open to everyone (customers often look here before sending a ticket).
Team Leads and Admins write them; drafts are only for the team."""
from types import SimpleNamespace

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import update

from .. import audit, db, desks, help as articles
from .. import permissions as can
from ..auth import login_required, not_for_demo, require
from ..dates import utcnow
from ..models import Article, ArticleVote, Category

bp = Blueprint("help", __name__, url_prefix="/help")


def _desk():
    if g.desk is None:
        abort(404)
    return g.desk


def _article(s, article_id):
    article = desks.get_in_desk(s, Article, article_id, _desk())
    if not can.can_view_article(g.user, article):
        abort(404)  # a draft doesn't exist for customers
    return article


@bp.get("")
def index():
    s = db.session()
    desk = _desk()
    q = request.args.get("q", "").strip()[:100]
    found = articles.search(s, g.user, desk, q) if q else articles.visible(s, g.user, desk)
    groups = {}
    for article in found:
        groups.setdefault(article.category.name if article.category else "General", []).append(article)
    return render_template("help/index.html", q=q, found=found, groups=groups if not q else {})


@bp.get("/<int:article_id>")
def show(article_id):
    s = db.session()
    article = _article(s, article_id)
    if not can.is_staff(g.user):
        s.execute(update(Article).where(Article.id == article.id).values(views=Article.views + 1)
                  .execution_options(synchronize_session=False))
        s.commit()
    helpful, not_helpful = articles.votes(s, article)
    return render_template("help/article.html", article=article, helpful=helpful, not_helpful=not_helpful,
                           my_vote=articles.my_vote(s, article, g.user))


@bp.post("/<int:article_id>/vote")
@login_required
def vote(article_id):
    s = db.session()
    article = _article(s, article_id)
    require(can.can_vote_article(g.user, article), "You can't rate this article.")
    helpful = request.form.get("helpful") == "1"
    row = s.query(ArticleVote).filter_by(article_id=article.id, user_id=g.user.id).one_or_none()
    if row is None:
        s.add(ArticleVote(article_id=article.id, user_id=g.user.id, helpful=helpful))
    else:
        row.helpful = helpful
    s.commit()
    flash("Thank you! Glad it helped." if helpful else "Thank you. If you still need help, send us a ticket.")
    return redirect(url_for("help.show", article_id=article.id))


@bp.get("/suggest")
@login_required
def suggest():
    """For the New Ticket page: articles that match the subject typed so far."""
    s = db.session()
    found = articles.suggest(s, g.user, g.desk, request.args.get("q", "")[:120])
    return jsonify(articles=[{"title": a.title, "url": url_for("help.show", article_id=a.id)} for a in found])


# ---------- Writing articles ----------

def _form(s):
    """Read the form: (the fields, errors). Nothing touches the database until it is all right."""
    chosen = request.form.get("category_id", "")
    category = desks.get_in_desk(s, Category, int(chosen), g.desk) if chosen.isdigit() else None
    fields = {"title": request.form.get("title", "").strip(), "body": request.form.get("body", "").strip(),
              "category_id": category.id if category else None, "is_published": request.form.get("publish") == "1"}
    return fields, articles.problems(fields["title"], fields["body"])


def _page(s, article, errors, preview=False):
    return render_template("help/edit.html", article=article, errors=errors, preview=preview,
                           categories=desks.categories(s, g.desk)), 400 if errors else 200


def _wants_preview():
    """The Preview button: show the article as readers will see it, and save nothing."""
    return request.form.get("action") == "preview"


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    require(can.can_manage_articles(g.user), "Only a Team Lead or an Admin can write Help Articles.")
    s = db.session()
    if request.method == "GET":
        return _page(s, SimpleNamespace(id=None, title="", body="", category_id=None, is_published=False), {})
    fields, errors = _form(s)
    if _wants_preview():
        return _page(s, SimpleNamespace(id=None, **fields), {}, preview=True)
    if errors:
        return _page(s, SimpleNamespace(id=None, **fields), errors)
    article = Article(desk_id=g.desk.id, created_by_id=g.user.id, updated_at=utcnow(), **fields)
    s.add(article)
    s.flush()
    audit.log(s, g.desk.id, g.user, audit.ARTICLE_ADDED, utcnow(), new=article.title,
              note="published" if article.is_published else "draft")
    s.commit()
    flash(f"Saved {article.title}." + ("" if article.is_published else " It is a draft: only the team sees it."))
    return redirect(url_for("help.show", article_id=article.id))


@bp.route("/<int:article_id>/edit", methods=["GET", "POST"])
@login_required
def edit(article_id):
    require(can.can_manage_articles(g.user), "Only a Team Lead or an Admin can change Help Articles.")
    s = db.session()
    article = desks.get_in_desk(s, Article, article_id, g.desk)
    if request.method == "GET":
        return _page(s, article, {})
    if request.form.get("action") == "delete":
        not_for_demo("The demo can't delete articles.")
        audit.log(s, g.desk.id, g.user, audit.ARTICLE_REMOVED, utcnow(), old=article.title)
        s.delete(article)
        s.commit()
        flash(f"Deleted {article.title}.")
        return redirect(url_for("help.index"))
    fields, errors = _form(s)
    if _wants_preview():
        return _page(s, SimpleNamespace(id=article.id, **fields), {}, preview=True)
    if errors:
        return _page(s, SimpleNamespace(id=article.id, **fields), errors)
    note = ("published" if fields["is_published"] and not article.is_published
            else "back to draft" if article.is_published and not fields["is_published"] else "")
    audit.log(s, g.desk.id, g.user, audit.ARTICLE_CHANGED, utcnow(), old=article.title, new=fields["title"], note=note)
    for name, value in fields.items():
        setattr(article, name, value)
    article.updated_at = utcnow()
    s.commit()
    flash(f"Saved {article.title}.")
    return redirect(url_for("help.show", article_id=article.id))
