"""Stage 6: Help Articles, pictures on tickets, and the customer's rating."""
import io

import pytest
from PIL import Image
from sqlalchemy import select

from tickettock import db
from tickettock.models import Article, Attachment, Notification, Ticket

from .conftest import browser, get_ticket, make_team, make_ticket, post


@pytest.fixture
def team(app):
    return make_team(app)


def page(client, url):
    return client.get(url).get_data(as_text=True)


def article(app, team, title="How to reset your password", body="Go to **Settings**, then Password.", publish=True):
    post(browser(app, team["lead"]), "/help/new", {"title": title, "body": body, **({"publish": "1"} if publish else {})})
    with app.app_context():
        return db.session().scalar(select(Article.id).where(Article.title == title))


# ---------- Help Articles ----------

def test_anyone_can_read_published_articles(app, team):
    article_id = article(app, team)
    visitor = app.test_client()
    assert "How to reset your password" in page(visitor, "/help")
    text = page(visitor, f"/help/{article_id}")
    assert "<strong>Settings</strong>" in text and "Log in" in text


def test_drafts_are_only_for_the_team(app, team):
    draft = article(app, team, title="Coming soon", publish=False)
    assert app.test_client().get(f"/help/{draft}").status_code == 404
    assert browser(app, team["customer"]).get(f"/help/{draft}").status_code == 404
    assert browser(app, team["agent"]).get(f"/help/{draft}").status_code == 200
    assert "Coming soon" not in page(browser(app, team["customer"]), "/help")


@pytest.mark.parametrize("who, status", [("customer", 403), ("agent", 403), ("lead", 200), ("admin", 200)])
def test_who_writes_articles(app, team, who, status):
    assert browser(app, team[who]).get("/help/new").status_code == status


def test_articles_cannot_run_scripts(app, team):
    article_id = article(app, team, body='<script>alert(1)</script> [click](javascript:alert(1))')
    text = page(app.test_client(), f"/help/{article_id}")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text  # shown as words, never run
    assert 'href="javascript' not in text  # the bad link stays plain text


def test_search_and_suggestions(app, team):
    article(app, team)
    article(app, team, title="Refunds", body="We refund within 5 days.")
    article(app, team, title="Password draft", publish=False)
    customer = browser(app, team["customer"])
    assert "How to reset your password" in page(customer, "/help?q=password")
    assert "Refunds" not in page(customer, "/help?q=password")
    answer = customer.get("/help/suggest?q=I forgot my password").get_json()
    assert [a["title"] for a in answer["articles"]] == ["How to reset your password"]  # never drafts
    assert browser(app, team["lead"]).get("/help/suggest?q=password").get_json()["articles"][0]["title"] == \
        "How to reset your password"


def test_editing_publishing_and_deleting(app, team):
    article_id = article(app, team, publish=False)
    lead = browser(app, team["lead"])
    post(lead, f"/help/{article_id}/edit", {"title": "Reset your password", "body": "Easy.", "publish": "1"})
    assert "Reset your password" in page(app.test_client(), f"/help/{article_id}")
    bad = post(lead, f"/help/{article_id}/edit", {"title": "", "body": "Kept what I typed"})
    assert bad.status_code == 400 and "Kept what I typed" in bad.get_data(as_text=True)
    post(lead, f"/help/{article_id}/edit", {"action": "delete"})
    assert app.test_client().get(f"/help/{article_id}").status_code == 404
    audit_page = page(browser(app, team["admin"]), "/audit?what=help")
    assert "Edited the Help Article Reset your password (published)" in audit_page
    assert "Deleted the Help Article Reset your password" in audit_page


def test_was_it_helpful(app, team):
    article_id = article(app, team)
    customer = browser(app, team["customer"])
    post(customer, f"/help/{article_id}/vote", {"helpful": "1"})
    post(customer, f"/help/{article_id}/vote", {"helpful": "0"})  # changed their mind: still one vote
    assert "0 found it helpful, 1 didn" in page(browser(app, team["lead"]), f"/help/{article_id}")
    assert post(app.test_client(), f"/help/{article_id}/vote", {"helpful": "1"}).status_code == 302  # to login


def test_customers_see_matching_articles_on_their_ticket(app, team):
    article(app, team)
    number = make_ticket(app, team["customer"], subject="Forgot my password")
    assert "These Might Help While You Wait" in page(browser(app, team["customer"]), f"/tickets/{number}")


# ---------- Pictures ----------

def picture(kind="PNG", size=(40, 30), exif=None):
    out = io.BytesIO()
    Image.new("RGB", size, (228, 87, 46)).save(out, kind, **({"exif": exif} if exif else {}))
    out.seek(0)
    return out


def test_a_new_ticket_with_pictures(app, team):
    customer = browser(app, team["customer"])
    response = post(customer, "/tickets/new", {
        "subject": "Error screen", "description": "See the picture.", "priority": "normal",
        "pictures": [(picture(), "screen.png"), (picture("JPEG"), "photo.jpg")]},
        content_type="multipart/form-data")
    assert response.status_code == 302
    with app.app_context():
        rows = db.session().scalars(select(Attachment)).all()
    assert [row.filename for row in rows] == ["screen.webp", "photo.webp"]
    shown = customer.get(f"/pictures/{rows[0].id}")
    assert shown.status_code == 200 and shown.mimetype == "image/webp" and "private" in shown.headers["Cache-Control"]
    assert customer.get(f"/pictures/{rows[0].id}/small").status_code == 200
    assert browser(app, team["other_customer"]).get(f"/pictures/{rows[0].id}").status_code == 404
    assert browser(app, team["agent"]).get(f"/pictures/{rows[0].id}").status_code == 200


def test_camera_details_are_removed(app, team):
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make
    customer = browser(app, team["customer"])
    post(customer, "/tickets/new", {"subject": "Photo", "description": "x", "priority": "normal",
                                    "pictures": [(picture("JPEG", exif=exif.tobytes()), "photo.jpg")]},
         content_type="multipart/form-data")
    with app.app_context():
        data = db.session().scalar(select(Attachment.data))
    assert b"PhoneMaker" not in data
    assert not Image.open(io.BytesIO(data)).getexif()


def test_files_that_are_not_pictures_are_refused(app, team):
    response = post(browser(app, team["customer"]), "/tickets/new", {
        "subject": "Hi", "description": "x", "priority": "normal", "pictures": [(io.BytesIO(b"%PDF-1.4 hello"), "a.pdf")]},
        content_type="multipart/form-data")
    assert response.status_code == 400 and "isn&#39;t a picture" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session().scalar(select(Attachment)) is None
        assert db.session().scalar(select(Ticket)) is None  # nothing saved: the customer fixes it and sends again


def test_at_most_three_pictures(app, team):
    response = post(browser(app, team["customer"]), "/tickets/new", {
        "subject": "Hi", "description": "x", "priority": "normal",
        "pictures": [(picture(), f"{n}.png") for n in range(4)]}, content_type="multipart/form-data")
    assert response.status_code == 400


def test_pictures_on_internal_notes_stay_with_the_team(app, team):
    number = make_ticket(app, team["customer"])
    post(browser(app, team["agent"]), f"/tickets/{number}/reply",
         {"body": "Logs attached.", "internal": "1", "pictures": [(picture(), "log.png")]},
         content_type="multipart/form-data")
    with app.app_context():
        row = db.session().scalar(select(Attachment))
    assert row.is_internal and row.message_id is not None
    assert browser(app, team["customer"]).get(f"/pictures/{row.id}").status_code == 404
    assert f"/pictures/{row.id}/small" not in page(browser(app, team["customer"]), f"/tickets/{number}")
    assert f"/pictures/{row.id}/small" in page(browser(app, team["lead"]), f"/tickets/{number}")


# ---------- Rating ----------

def test_the_customer_rates_a_solved_ticket_once(app, team):
    number = make_ticket(app, team["customer"], status="resolved", assignee_id=team["agent"], resolution="Fixed.")
    customer = browser(app, team["customer"])
    assert "How Did We Do?" in page(customer, f"/tickets/{number}")
    post(customer, f"/tickets/{number}/rate", {"stars": "5", "comment": "Fast and kind!"})
    ticket = get_ticket(app, number)
    assert (ticket.rating, ticket.rating_comment) == (5, "Fast and kind!")
    assert post(customer, f"/tickets/{number}/rate", {"stars": "1"}).status_code == 403
    assert "You rated our help 5 out of 5" in page(customer, f"/tickets/{number}")
    with app.app_context():
        told = db.session().scalar(select(Notification).where(Notification.kind == "rated"))
    assert told.user_id == team["agent"] and told.detail == "5 out of 5"
    assert "Fast and kind!" in page(browser(app, team["agent"]), f"/tickets/{number}")


def test_open_tickets_cannot_be_rated(app, team):
    number = make_ticket(app, team["customer"], status="open")
    assert post(browser(app, team["customer"]), f"/tickets/{number}/rate", {"stars": "4"}).status_code == 403


def test_only_the_customer_rates(app, team):
    number = make_ticket(app, team["customer"], status="closed")
    assert post(browser(app, team["agent"]), f"/tickets/{number}/rate", {"stars": "5"}).status_code == 403
    assert post(browser(app, team["other_customer"]), f"/tickets/{number}/rate", {"stars": "5"}).status_code == 404


def test_stars_must_be_one_to_five(app, team):
    number = make_ticket(app, team["customer"], status="closed")
    post(browser(app, team["customer"]), f"/tickets/{number}/rate", {"stars": "9"})
    assert get_ticket(app, number).rating is None
