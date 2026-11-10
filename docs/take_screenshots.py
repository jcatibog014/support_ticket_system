r"""Take the README screenshots (docs/screenshots/*.png).

Retake them in every pull request that visibly changes a page, and add a new
one when a page or feature is added: the README shows everything Ticket Tock
can do. tests/test_readme.py fails if the pictures and the README disagree.

Every picture comes from "Try demo", so only the made-up Kape Cloud people
show. Use an empty practice database, never the live one. It uses Microsoft
Edge, which Windows already has, through Playwright. Playwright is only a tool
for this script, so it is not in requirements.txt (borrow another project's):

    $env:DATABASE_URL = "sqlite:///C:/Temp/tickettock-screenshots.sqlite"
    .venv\Scripts\flask --app tickettock upgrade-db
    .venv\Scripts\flask --app tickettock create-admin          (any name: the login page needs a desk)
    .venv\Scripts\flask --app tickettock run --debug --port 5010    (keep this one running)
    ..\expense_tracker\.venv\Scripts\python docs\take_screenshots.py

Every picture is one screen (no full-page shots), so they are all the same
size and the README table stays even. Add a line to PAGES when a page is added.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5010"
OUT = Path(__file__).parent / "screenshots"
DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}

# (file name, address, theme, screen size, View As role or None for the visitor/no login).
# "{Subject}" is the address of the demo ticket with that subject; "#..." scrolls there.
PAGES = [
    ("login.png", "/login", "light", DESKTOP, None),
    ("home.png", "/", "light", DESKTOP, "lead"),
    ("queue.png", "/queue?tab=open", "light", DESKTOP, "lead"),
    ("queue-overdue.png", "/queue?tab=overdue", "light", DESKTOP, "agent"),
    ("ticket.png", "{Charged twice for November}", "light", DESKTOP, "agent"),
    ("ticket-dark.png", "{Charged twice for November}", "dark", DESKTOP, "agent"),
    ("ticket-history.png", "{Export button does nothing}#history", "light", DESKTOP, "lead"),
    ("escalated.png", "{Page is very slow}", "light", DESKTOP, "lead"),
    ("my-tickets.png", "/tickets", "light", DESKTOP, "customer"),
    ("new-ticket.png", "/tickets/new", "light", DESKTOP, "customer"),
    ("customer-ticket.png", "{Export button does nothing}", "light", DESKTOP, "customer"),
    ("rating.png", "{Wrong name on my receipt}#rate", "light", DESKTOP, "customer"),
    ("inbox.png", "/inbox", "light", DESKTOP, "agent"),
    ("saved-replies.png", "/saved-replies", "light", DESKTOP, "lead"),
    ("insights.png", "/insights?days=30", "light", DESKTOP, "lead"),
    ("insights-dark.png", "/insights?days=30", "dark", DESKTOP, "lead"),
    ("audit-log.png", "/audit", "light", DESKTOP, "lead"),
    ("help.png", "/help", "light", DESKTOP, None),
    ("help-search.png", "/help?q=password", "light", DESKTOP, "customer"),
    ("staff.png", "/admin/staff", "light", DESKTOP, "admin"),
    ("categories.png", "/admin/categories", "light", DESKTOP, "admin"),
    ("time-limits.png", "/admin/hours", "light", DESKTOP, "admin"),
    ("settings.png", "/settings", "light", DESKTOP, "lead"),
    ("phone-queue.png", "/queue?tab=open", "light", PHONE, "agent"),
    ("phone-ticket.png", "{Export button does nothing}", "light", PHONE, "customer"),
]


def start_demo(browser, theme, size):
    page = browser.new_page(viewport=size, color_scheme=theme)
    page.goto(f"{BASE}/login")
    page.get_by_role("button", name="Try demo").click()
    page.wait_for_url(f"{BASE}/")
    return page


def ticket_urls(page):
    """{subject: "/tickets/12"} from the queue's tabs (the demo starts as a Team Lead, who sees them all)."""
    urls = {}
    for tab in ("open", "waiting", "resolved", "closed"):
        page.goto(f"{BASE}/queue?tab={tab}")
        for link in page.locator(".stub-title").all():
            urls[link.inner_text().strip()] = link.get_attribute("href")
    return urls


def view_as(page, role):
    page.goto(f"{BASE}/settings")
    page.select_option("#view-as-settings", role)
    page.wait_for_load_state("networkidle")


def save(page, name):
    page.wait_for_load_state("networkidle")  # fonts are in
    page.screenshot(path=str(OUT / name))
    print("saved", name)


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        for name, address, theme, size, role in PAGES:
            if role is None:
                page = browser.new_page(viewport=size, color_scheme=theme)
            else:
                page = start_demo(browser, theme, size)
                urls = ticket_urls(page) if address.startswith("{") else {}
                if role != "lead":
                    view_as(page, role)
                if address.startswith("{"):
                    subject, _, rest = address[1:].partition("}")
                    address = urls[subject] + rest
            page.goto(BASE + address)
            if name == "new-ticket.png":
                page.locator("input[name=subject]").press_sequentially("I forgot my password", delay=30)
                page.wait_for_timeout(1200)  # the Help Article suggestions
            if "#" in address:
                page.locator("#" + address.split("#")[1]).scroll_into_view_if_needed()
                page.evaluate("window.scrollBy(0, -160)")  # a little of what comes before, for context
            save(page, name)
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
