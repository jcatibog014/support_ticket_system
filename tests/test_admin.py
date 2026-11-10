"""The Admin's pages: staff, invite links, roles and categories."""
import pytest
from sqlalchemy import select

from tickettock import db
from tickettock.models import Category, Invite, User

from .conftest import browser, is_logged_in, make_team, make_user, post


@pytest.fixture
def team(app):
    return make_team(app)


@pytest.mark.parametrize("who, status", [("customer", 403), ("agent", 403), ("lead", 403), ("admin", 200)])
def test_only_admins_open_the_admin_pages(app, team, who, status):
    client = browser(app, team[who])
    assert client.get("/admin/staff").status_code == status
    assert client.get("/admin/categories").status_code == status


def test_logged_out_people_go_to_login(client):
    response = client.get("/admin/staff")
    assert response.status_code == 302 and "/login" in response.location


def test_the_menu_shows_admin_links_to_admins_only(app, team):
    assert "/admin/staff" in browser(app, team["admin"]).get("/").get_data(as_text=True)
    assert "/admin/staff" not in browser(app, team["lead"]).get("/").get_data(as_text=True)


def test_making_an_invite_link(app, team):
    client = browser(app, team["admin"])
    response = post(client, "/admin/staff/invite", {"role": "lead"})
    assert response.status_code == 302 and "link=" in response.location
    page = client.get(response.location).get_data(as_text=True)
    assert "/join/" in page
    with app.app_context():
        assert db.session().scalar(select(Invite.role)) == "lead"


def test_an_invite_must_have_a_staff_role(app, team):
    client = browser(app, team["admin"])
    assert post(client, "/admin/staff/invite", {"role": "customer"}).status_code == 400


def test_non_admins_cannot_make_invites(app, team):
    assert post(browser(app, team["lead"]), "/admin/staff/invite", {"role": "admin"}).status_code == 403


def test_changing_a_role_logs_the_person_out(app, team):
    agent = browser(app, team["agent"])
    admin = browser(app, team["admin"])
    assert post(admin, f"/admin/staff/{team['agent']}/role", {"role": "lead"}).status_code == 302
    with app.app_context():
        assert db.session().get(User, team["agent"]).role == "lead"
    assert not is_logged_in(agent)


def test_an_admin_cannot_change_their_own_role(app, team):
    admin = browser(app, team["admin"])
    assert post(admin, f"/admin/staff/{team['admin']}/role", {"role": "agent"}).status_code == 403


def test_customers_are_not_on_the_staff_list(app, team):
    admin = browser(app, team["admin"])
    assert post(admin, f"/admin/staff/{team['customer']}/role", {"role": "admin"}).status_code == 404


def test_switching_a_staff_account_off_and_on(app, team):
    admin = browser(app, team["admin"])
    post(admin, f"/admin/staff/{team['agent']}/active", {"active": "0"})
    with app.app_context():
        assert db.session().get(User, team["agent"]).is_active is False
    post(admin, f"/admin/staff/{team['agent']}/active", {"active": "1"})
    with app.app_context():
        assert db.session().get(User, team["agent"]).is_active is True


def test_staff_on_another_desk_are_not_found(app, team):
    with app.app_context():
        from tickettock import desks
        s = db.session()
        other = desks.create_desk(s, "Other desk", is_demo=True)
        s.commit()
        other_id = other.id
    stranger = make_user(app, "Lia Cruz", role="agent", desk_id=other_id)
    admin = browser(app, team["admin"])
    assert post(admin, f"/admin/staff/{stranger}/role", {"role": "admin"}).status_code == 404


def test_categories_add_rename_hide_and_reorder(app, team):
    admin = browser(app, team["admin"])
    assert post(admin, "/admin/categories", {"name": "Shipping", "description": "Where is my order?"}).status_code == 302
    assert post(admin, "/admin/categories", {"name": "Shipping"}).status_code == 400  # taken
    with app.app_context():
        s = db.session()
        shipping = s.scalar(select(Category).where(Category.name == "Shipping"))
        first = s.scalar(select(Category).order_by(Category.position))
    post(admin, f"/admin/categories/{shipping.id}", {"name": "Delivery", "description": ""})
    post(admin, f"/admin/categories/{shipping.id}", {"action": "hide"})
    post(admin, f"/admin/categories/{shipping.id}", {"action": "up"})
    with app.app_context():
        s = db.session()
        row = s.get(Category, shipping.id)
        assert row.name == "Delivery" and row.is_active is False
        ordered = [c.id for c in s.scalars(select(Category).order_by(Category.position))]
        assert ordered.index(shipping.id) == len(ordered) - 2
        assert ordered[0] == first.id
