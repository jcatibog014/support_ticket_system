"""The demo: a sample help desk for "Kape Cloud", a made-up coffee subscription app.

Every Try Demo makes its own desk (deleted after 24 hours), with a team, customers,
Help Articles, saved replies, about 30 tickets in every state and 3 months of closed ones. The tickets are
not typed straight into the tables: each one's story (sent, taken, replied,
waited, resolved, rated...) is played through the real functions, in time order.
So the history chain, the SLA clocks, the Inbox and the escalations are exactly
what the real desk would have, and a page never shows something impossible.

Everyone is made up. Every page the demo can open must show an example: add
sample data here with every new feature (tests/test_demo.py checks each page).
"""
import io
import secrets
from datetime import datetime, time, timedelta

from PIL import Image, ImageDraw
from sqlalchemy import select
from werkzeug.datastructures import FileStorage
from werkzeug.security import generate_password_hash

from . import audit, closing, desks, hours, invites, photos, tick, tickets, tools, workflow
from .dates import from_local, to_local
from .models import (ADMIN, AGENT, CUSTOMER, LEAD, Article, ArticleVote, Category, SavedReply, SavedView, Ticket,
                     User)

DESK_NAME = "Kape Cloud Support"
DOMAIN = "kapecloud.example"

# (name, email name, role). The first of each role is who View As plays.
PEOPLE = [
    ("Sam Rivera", "sam", ADMIN),
    ("Ben Torres", "ben", LEAD),
    ("Ana Reyes", "ana", AGENT),
    ("Carlo Diaz", "carlo", AGENT),
    ("Lia Cruz", "lia", AGENT),
    ("Maria Santos", "maria", CUSTOMER),
    ("Jose Rizal", "jose", CUSTOMER),
    ("Grace Lim", "grace", CUSTOMER),
    ("Paolo Reyes", "paolo", CUSTOMER),
    ("Nina Garcia", "nina", CUSTOMER),
    ("Tomas Bautista", "tomas", CUSTOMER),
    ("Elena Cruz", "elena", CUSTOMER),
]
VIEW_AS = {CUSTOMER: "maria", AGENT: "ana", LEAD: "ben", ADMIN: "sam"}

ARTICLES = [
    ("How to reset your password", "Login and account",
     "1. Open the **Log In** page.\n2. Press *Forgot your password?*\n3. Type your email and the recovery code you saved.\n\n"
     "No recovery code? Send us a ticket and we'll check it's really you."),
    ("Why was I charged twice?", "Billing",
     "Sometimes your bank shows a **hold** and the real payment at the same time. The hold goes away by itself "
     "in 3 to 5 days.\n\nIf both are still there after 5 days, send us a ticket with a screenshot of your statement."),
    ("Pausing or skipping a delivery", "Orders and delivery",
     "Open **My Plan**, choose the box, and press **Skip this one**. You can skip up to 3 boxes in a row.\n\n"
     "To pause for longer, choose **Pause plan** and pick the week you want it back."),
    ("Changing your delivery address", "Orders and delivery",
     "Go to **Settings**, then **Address**. Changes made before Thursday 6 PM apply to the next box."),
    ("The app is slow or blank", "Bug report",
     "- Close the app and open it again.\n- Check that you have the newest version.\n- On a computer, try another browser.\n\n"
     "Still blank? Send us a ticket and tell us your phone or browser."),
    ("Getting a refund", "Billing",
     "Refunds go back to the card you paid with, in **5 to 7 working days**. A box that arrived damaged is refunded "
     "in full: just send a photo."),
]
DRAFT = ("Gift subscriptions (coming soon)", "Question", "Notes for the team: launch date not set yet.")

SAVED_REPLIES = [
    ("Thanks, looking into it", "Hi {name}, thank you for writing in! I'm looking into this now and will get back to you soon.\n\n{agent}"),
    ("Need a screenshot", "Hi {name}, could you send a screenshot of what you see? It helps us find the problem faster.\n\n{agent}"),
    ("Refund on its way", "Hi {name}, the refund for {ticket} is on its way. It takes 5 to 7 working days to show on your card.\n\n{agent}"),
    ("Closing for now", "Hi {name}, we haven't heard back in a while, so I'm marking {ticket} as solved. Reply any time if you still need help.\n\n{agent}"),
]

# The stories. Each: customer, subject, words, category, priority, sent (days ago, hour in Manila), and the
# steps after it: (hours later, what, ...). Times that would land in the future are left out.
STORIES = [
    # ----- Waiting for someone (new, unassigned) -----
    ("grace", "Can't log in on my phone", "The app says my password is wrong, but it works on my laptop.",
     "Login and account", "high", (0, 9), []),
    ("tomas", "Wrong grind in my box", "I asked for whole beans but got ground coffee this week.",
     "Orders and delivery", "normal", (0, 10), [(0.2, "picture", "tomas", "box")]),
    ("nina", "Do you deliver to Cebu?", "We're moving to Cebu next month. Can I keep my plan?",
     "Question", "low", (1, 15), []),
    ("paolo", "Charged but no box this month", "I was charged on Nov 1 but nothing came. Order KC-44821.",
     "Billing", "high", (3, 9), []),  # nobody replied: the timer escalates it
    # ----- Open / In Progress -----
    ("maria", "Export button does nothing", "When I press Export on the Reports page, nothing downloads.",
     "Bug report", "normal", (2, 11), [(0.5, "picture", "maria", "export"), (1, "take", "carlo"),
                                       (1.2, "reply", "carlo", "Thanks Maria! Which browser do you use?", "waiting"),
                                       (20, "reply", "maria", "Chrome on Windows 11."),  # back to In Progress by itself
                                       (22, "note", "carlo", "Reproduced it on Chrome. Looks like the new download blocker. Asking the web team.")]),
    ("jose", "Charged twice for November", "My card shows two payments of 499 on Nov 3.",
     "Billing", "high", (1, 10), [(0.5, "assign", "ben", "ana"), (0.6, "priority", "ana", "urgent"),
                                  (0.7, "move", "ana", "in_progress"),
                                  (0.8, "reply", "ana", "Hi Jose, sorry about that! I can see both payments. I'm asking billing to refund one now."),
                                  (0.9, "note", "ana", "Refund request is BIL-2231. Usually takes 2 days.")]),
    ("elena", "Page is very slow", "The dashboard takes 30 seconds to load since Monday.",
     "Bug report", "normal", (2, 14), [(2, "take", "lia"), (2.2, "reply", "lia", "Hi Elena, thanks for telling us. Is it slow on wifi and on mobile data?"),
                                       (3, "escalate", "lia", "Three customers say the same. Can someone check the servers?")]),
    ("grace", "Missing loyalty points", "I had 1,200 points last week and now it says 900.",
     "Question", "normal", (1, 16), [(0.3, "take", "ben"), (0.4, "move", "ben", "in_progress")]),
    ("nina", "Box arrived damaged", "The bag was torn and the beans spilled everywhere.",
     "Orders and delivery", "high", (0, 8), [(0.1, "picture", "nina", "damaged"), (0.4, "assign", "ben", "lia"),
                                             (0.5, "reply", "lia", "Oh no, I'm so sorry, Nina! A new box goes out today, no charge.")]),
    ("paolo", "Promo code doesn't work", "The code KAPE20 says it has run out, but the email says it works until Nov 30.",
     "Billing", "normal", (1, 13), [(0.2, "take", "ana"), (0.3, "reply", "ana", "Hi Paolo, let me check that code with the marketing team.")]),
    # ----- Waiting on Customer -----
    ("tomas", "Can't change my plan size", "The Change Plan button is grey.",
     "Login and account", "normal", (2, 9), [(1, "take", "ana"), (1.1, "reply", "ana", "Hi Tomas, could you send a screenshot of what you see?", "waiting")]),
    ("elena", "Receipt for my company", "I need an official receipt with my company name for October.",
     "Billing", "low", (4, 10), [(3, "take", "carlo"), (3.2, "reply", "carlo", "Sure! What company name and address should be on it?", "waiting")]),
    # ----- Resolved (can still be reopened and rated) -----
    ("jose", "How do I change my email?", "I got a new email address. Where do I change it?",
     "Question", "low", (3, 11), [(1, "take", "ana"), (1.2, "reply", "ana", "Go to Settings, then Your Email. You can change it there."),
                                  (1.3, "resolve", "ana", "Showed where to change the email in Settings.")]),
    ("maria", "Wrong name on my receipt", "My receipt says Mario instead of Maria.",
     "Billing", "low", (2, 15), [(0.5, "take", "lia"), (0.7, "reply", "lia", "Fixed! The new receipt is in your account now."),
                                 (0.8, "resolve", "lia", "Corrected the name and sent a new receipt.")]),  # left for View As to rate
    ("grace", "Skip next week's box", "I'm travelling next week. Can you skip my box?",
     "Orders and delivery", "normal", (5, 9), [(2, "reply", "ana", "Done! Next week's box is skipped. You can also do it yourself under My Plan."),
                                               (2.1, "resolve", "ana", "Skipped the box and showed the Skip button."),
                                               (3, "rate", "grace", 4, "")]),
    ("nina", "App crashes when I open Rewards", "Every time I tap Rewards the app closes.",
     "Bug report", "high", (6, 10), [(0.5, "take", "carlo"), (0.6, "move", "carlo", "in_progress"),
                                     (2, "reply", "carlo", "We found the bug and fixed it in version 3.4.1. Please update the app."),
                                     (2.1, "resolve", "carlo", "Bug in the Rewards page, fixed in 3.4.1."),
                                     (5, "rate", "nina", 3, "It works now, but it took a while.")]),
    ("paolo", "Coffee too bitter", "Is there a lighter roast? This one is too strong for me.",
     "Question", "low", (4, 16), [(1, "take", "lia"), (1.2, "reply", "lia", "Try our Morning Light roast: it's softer. I added a sample to your next box!"),
                                  (1.3, "resolve", "lia", "Suggested the Morning Light roast, sample added.")]),
    ("tomas", "Where is my receipt for October?", "I can't find the October receipt in my account.",
     "Billing", "low", (0, 8), [(0.5, "take", "ben"), (0.7, "reply", "ben", "Hi Tomas, I sent it to your email again. It's also under Settings, Receipts."),
                                (0.8, "resolve", "ben", "Sent the October receipt again.")]),
    # ----- Closed -----
    ("tomas", "Delivery came late", "My box came on Saturday instead of Thursday.",
     "Orders and delivery", "normal", (12, 10), [(1, "take", "ana"), (1.3, "reply", "ana", "Sorry, Tomas! The courier had a backlog. Here is a 10% voucher for next time."),
                                                 (1.4, "resolve", "ana", "Courier delay, voucher given."),
                                                 (3, "rate", "tomas", 4, "Thanks for the voucher.")]),  # the timer closes it
    ("elena", "Update my card", "My card expired. How do I add a new one?",
     "Billing", "normal", (15, 14), [(0.5, "take", "carlo"), (0.6, "reply", "carlo", "Go to Settings, then Payment, and press Add card."),
                                     (0.7, "resolve", "carlo", "Showed where to add a card."),
                                     (5, "close", "elena"), (5.1, "rate", "elena", 5, "")]),
    ("maria", "Two accounts by mistake", "I made a second account with my work email. Can you join them?",
     "Login and account", "normal", (20, 9), [(1, "assign", "ben", "ana"), (1.2, "move", "ana", "in_progress"),
                                              (3, "reply", "ana", "Done! Everything is in your first account now."),
                                              (3.1, "resolve", "ana", "Merged the two accounts."),
                                              (4, "close", "maria"), (4.1, "rate", "maria", 5, "Perfect.")]),
    ("jose", "Beans were stale", "The roast date on the bag was 2 months ago.",
     "Orders and delivery", "high", (9, 11), [(0.3, "take", "lia"), (0.4, "reply", "lia", "That shouldn't happen. A fresh bag is on its way and I've told the warehouse."),
                                              (0.5, "resolve", "lia", "Sent a fresh bag, warehouse told."),
                                              (2, "rate", "jose", 2, "The new bag was fine, but this is the second time.")]),
    ("grace", "Question about gift cards", "Can I buy a gift card for a friend?",
     "Question", "low", (18, 15), [(0.5, "take", "carlo"), (0.6, "reply", "carlo", "Not yet, but it's coming next month!"),
                                   (0.7, "resolve", "carlo", "Gift cards aren't out yet.")]),
    # ----- Cancelled, and a merged duplicate -----
    ("paolo", "Forgot my password", "Never mind, I found it.", "Login and account", "low", (8, 9),
     [(0.1, "cancel", "paolo")]),
    ("grace", "Login doesn't work on phone", "Same problem as before: wrong password on the phone app.",
     "Login and account", "normal", (0, 9.5), [(0.5, "merge", "ben", 0)]),  # into the first story
    # ----- Escalated by the timer (late, nobody on it) -----
    ("elena", "Can't download my invoice", "The Download button on Invoices gives an error.",
     "Billing", "urgent", (2, 10), []),
    ("tomas", "Subscription renewed after I cancelled", "I cancelled on Oct 28 but was charged again on Nov 1.",
     "Billing", "high", (4, 15), [(1, "take", "carlo")]),
]

# ----- The desk's past: closed tickets from 3 weeks to 3 months ago -----
# So Insights' 7, 30 and 90 days each show something different. The team gets better over time:
# three months ago replies were slow and ratings low, a month ago better, and lately quick.
PAST = [
    ("Box came a day late", "My box was supposed to come on Thursday but came on Friday.", "Orders and delivery", "normal",
     "Courier delay, said sorry."),
    ("How do I pause my plan?", "I'm away for a month. Can I pause instead of cancel?", "Question", "low",
     "Showed Pause plan in My Plan."),
    ("Charged the old price", "The price went down but I was charged the old one.", "Billing", "high",
     "Refunded the difference."),
    ("Can't reset my password", "The reset page says my code is wrong.", "Login and account", "normal",
     "Checked who it was and made a new code."),
    ("App logs me out", "The app logs me out every time I close it.", "Bug report", "normal",
     "Fixed in the next app version."),
    ("Wrong beans in my box", "I ordered the dark roast but got the light one.", "Orders and delivery", "normal",
     "Sent the right beans."),
    ("Need a receipt with my TIN", "My company needs my TIN on the receipt.", "Billing", "low",
     "Sent a new receipt with the TIN."),
    ("Is the coffee organic?", "Are your beans organic?", "Question", "low",
     "Explained which roasts are organic."),
    ("Double charge this month", "I see two payments for the same box.", "Billing", "urgent",
     "One payment was a bank hold, explained."),
    ("Address change didn't save", "I changed my address but the box went to the old one.", "Orders and delivery", "high",
     "Fixed the address, new box sent."),
    ("Rewards points missing", "My points from last month are gone.", "Question", "normal",
     "Added the missing points back."),
    ("Page won't load on Safari", "The website stays blank on my iPad.", "Bug report", "high",
     "Safari bug, fixed on the website."),
]
PAST_CUSTOMERS = ("maria", "jose", "grace", "paolo", "nina", "tomas", "elena")
PAST_AGENTS = ("ana", "carlo", "lia", "ana", "carlo", "ben")


def _past_stories():
    """About 36 closed tickets, one every 2 days from 21 to 88 days ago (oldest last), as STORIES-style tuples."""
    stories = []
    for number in range(36):
        days_ago = 21 + round(number * 67 / 35)
        subject, words, category, priority, resolution = PAST[number % len(PAST)]
        customer = PAST_CUSTOMERS[number % len(PAST_CUSTOMERS)]
        agent = PAST_AGENTS[number % len(PAST_AGENTS)]
        # Hours to the first reply and to resolved, and the stars, by how long ago it was.
        if days_ago > 60:
            reply_after, solve_after, stars = 30, 150, (2, 3, 3, 4)[number % 4]
        elif days_ago > 30:
            reply_after, solve_after, stars = 6, 30, (3, 4, 4, 5)[number % 4]
        else:
            reply_after, solve_after, stars = 1.5, 8, (4, 5, 5, 5)[number % 4]
        steps = [(reply_after - 0.2, "take", agent),
                 (reply_after, "reply", agent, "Hi! Thanks for telling us. Let me sort this out for you."),
                 (solve_after, "resolve", agent, resolution)]
        if number % 3:  # most customers rate; the timer closes them all a week after resolved
            steps.append((solve_after + 20, "rate", customer, stars, ""))
        stories.append((customer, subject, words, category, priority, (days_ago, 9 + number % 7), steps))
    return stories


PICTURES = {
    "box": ((244, 236, 222), (120, 84, 54), "Beans: GROUND"),
    "export": ((250, 250, 250), (200, 64, 27), "Export"),
    "damaged": ((236, 230, 220), (90, 60, 40), "Torn bag"),
}


def _picture(kind):
    """A small made-up screenshot or photo (drawn here, so there is nothing to download)."""
    background, mark, words = PICTURES[kind]
    image = Image.new("RGB", (720, 450), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 720, 54], fill=(31, 42, 68))
    draw.rectangle([250, 180, 470, 250], fill=mark)
    draw.text((290, 205), words, fill=(255, 255, 255))
    out = io.BytesIO()
    image.save(out, "PNG")
    out.seek(0)
    return FileStorage(out, filename=f"{kind}.png", content_type="image/png")


def _at(now, days_ago, hour):
    day = to_local(now).date() - timedelta(days=days_ago)
    return from_local(datetime.combine(day, time()) + timedelta(hours=hour))


def build(s, now):
    """A new demo desk with everything in it. Returns (desk, {email name: user}). The caller commits."""
    desk = desks.create_desk(s, DESK_NAME, is_demo=True, demo_expires_at=now + timedelta(hours=24))
    s.add(Category(desk_id=desk.id, name="Orders and delivery", description="Boxes, delivery days, skipping.",
                   position=9, auto_assign=True))
    s.flush()
    hours.add_ph_holidays(s, desk, to_local(now).year)
    people = {}
    for name, key, role in PEOPLE:
        # Nobody logs in to a demo user with a password (View As does it), so a quick hash is enough.
        user = User(desk_id=desk.id, name=name, email=f"{key}@{DOMAIN}", role=role,
                    password_hash=generate_password_hash(secrets.token_urlsafe(24), "pbkdf2:sha256:1000"))
        user.desk = desk
        s.add(user)
        # A random start: a deleted demo's old cookie (user id + version) can never fit a new demo's user.
        user.session_version = secrets.randbelow(10 ** 9) + 2
        user.created_at = now - timedelta(days=100)  # before the oldest past ticket (PAST)
        people[key] = user
    s.flush()
    categories = {c.name: c for c in s.scalars(select(Category).where(Category.desk_id == desk.id))}
    _content(s, desk, people, categories, now)
    _stories(s, desk, people, categories, now)
    # The timer: escalates the late tickets and closes the ones resolved a week ago, like on the real desk.
    tick.run_desk(s, desk, now)
    _after(s, desk, people, now)
    return desk, people


def _content(s, desk, people, categories, now):
    ben = people["ben"]
    for number, (title, category, body) in enumerate(ARTICLES):
        s.add(Article(desk_id=desk.id, title=title, body=body, category_id=categories[category].id, is_published=True,
                      created_by_id=ben.id, updated_at=now - timedelta(days=30 - number), views=40 - 5 * number))
    title, category, body = DRAFT
    s.add(Article(desk_id=desk.id, title=title, body=body, category_id=categories[category].id, is_published=False,
                  created_by_id=ben.id, updated_at=now - timedelta(days=1)))
    for title, body in SAVED_REPLIES:
        s.add(SavedReply(desk_id=desk.id, title=title, body=body, created_by_id=ben.id, created_at=now - timedelta(days=90)))
    s.flush()
    first = s.scalar(select(Article).where(Article.desk_id == desk.id).order_by(Article.id))
    for key, helpful in (("maria", True), ("jose", True), ("grace", False)):
        s.add(ArticleVote(article_id=first.id, user_id=people[key].id, helpful=helpful))
    s.add(SavedView(user_id=ben.id, name="Urgent and open", query="tab=open&priority=urgent"))
    s.add(SavedView(user_id=people["ana"].id, name="Billing", query=f"tab=open&category={categories['Billing'].id}"))
    # The desk's own history, before the tickets (the oldest is 88 days ago): the team joining, Ben made
    # Team Lead, the settings.
    sam = people["sam"]
    for number, key in enumerate(("ben", "ana", "carlo", "lia")):
        person = people[key]
        audit.log(s, desk.id, person, audit.JOINED, now - timedelta(days=100, hours=-number), field="role",
                  new=AGENT, note=person.email)
    audit.log(s, desk.id, sam, audit.ROLE_CHANGED, now - timedelta(days=95), field="role", old=AGENT, new=LEAD,
              note=f"{ben.name} ({ben.email})")
    audit.log(s, desk.id, sam, audit.CATEGORY_ADDED, now - timedelta(days=94), new="Orders and delivery")
    audit.log(s, desk.id, sam, audit.CATEGORY_CHANGED, now - timedelta(days=94), field="auto_assign", old="no",
              new="yes", note="Orders and delivery")
    audit.log(s, desk.id, ben, audit.REPLY_SAVED, now - timedelta(days=90), new=SAVED_REPLIES[0][0])


def _stories(s, desk, people, categories, now):
    """Play every story's steps through the real functions, all stories together in time order."""
    steps = []
    for index, (customer, subject, words, category, priority, (days_ago, hour), story_steps) in enumerate(STORIES + _past_stories()):
        sent = _at(now, days_ago, hour)
        if days_ago == 0:
            # Today's stories must have happened already, whatever the time of day the demo starts:
            # early in the morning they are moved back so their last step is half an hour ago.
            last_step = max((later for later, *_ in story_steps), default=0)
            sent = min(sent, now - timedelta(hours=last_step + 0.5, minutes=7 * (len(STORIES) - index)))
        steps.append((sent, index, "create", (customer, subject, words, category, priority)))
        for later, what, *args in story_steps:
            steps.append((sent + timedelta(hours=later), index, what, tuple(args)))
            if what == "resolve":
                # The timer closes it 7 days later (if nobody reopened it), at that moment, not when the demo starts.
                steps.append((sent + timedelta(hours=later) + closing.REOPEN_WINDOW + timedelta(minutes=5), index, "autoclose", ()))
    made = {}
    last_message = {}
    for moment, index, what, args in sorted(steps, key=lambda step: (step[0], step[1])):
        if moment > now - timedelta(minutes=2):
            continue
        ticket = made.get(index)
        if what == "create":
            customer, subject, words, category, priority = args
            made[index] = tickets.create(s, people[customer], subject, words, moment, category=categories[category],
                                         priority=priority)
        elif ticket is None or (ticket.status in ("closed", "cancelled") and what != "rate"):
            continue
        elif what == "take":
            tickets.assign(s, ticket, people[args[0]], people[args[0]], moment)
        elif what == "assign":
            tickets.assign(s, ticket, people[args[0]], people[args[1]], moment)
        elif what == "priority":
            tickets.set_priority(s, ticket, people[args[0]], args[1], moment)
        elif what in ("reply", "note"):
            author = people[args[0]]
            last_message[index] = tickets.reply(s, ticket, author, args[1], moment, internal=what == "note")
            if len(args) > 2 and args[2] == "waiting":
                workflow.move(s, ticket, "waiting", author, moment)
        elif what == "move":
            workflow.move(s, ticket, args[1], people[args[0]], moment)
        elif what == "resolve":
            workflow.move(s, ticket, "resolved", people[args[0]], moment, resolution=args[1])
        elif what == "close":
            workflow.move(s, ticket, "closed", people[args[0]], moment)
        elif what == "autoclose":
            if ticket.status == "resolved" and ticket.resolved_at <= moment - closing.REOPEN_WINDOW:
                workflow.move(s, ticket, "closed", None, moment, note=closing.NOTE)
        elif what == "cancel":
            workflow.move(s, ticket, "cancelled", people[args[0]], moment)
        elif what == "rate":
            tickets.rate(s, ticket, people[args[0]], args[1], args[2], moment)
        elif what == "escalate":
            tick.escalate(s, ticket, moment, args[1], actor=people[args[0]])
        elif what == "merge":
            workflow.merge(s, ticket, made[args[1]], people[args[0]], moment)
        elif what == "picture":
            s.flush()
            s.add(photos.make(ticket, people[args[0]], _picture(args[1]), moment))
            audit.log(s, desk.id, people[args[0]], audit.ATTACHED, moment, ticket=ticket, new="1")
    s.flush()


def _after(s, desk, people, now):
    """Final touches: some notices already read, and Carlo looking at a ticket right now."""
    from .models import Notification
    for notice in s.scalars(select(Notification).where(Notification.desk_id == desk.id,
                                                        Notification.created_at < now - timedelta(days=3))):
        notice.read_at = notice.created_at + timedelta(hours=1)
    # A staff invite nobody has used yet (the link itself is thrown away: it is only an example).
    invites.create(s, desk, AGENT, people["sam"], now)
    audit.log(s, desk.id, people["sam"], audit.INVITE_MADE, now, field="role", new=AGENT)
    busy = s.scalar(select(Ticket).where(Ticket.desk_id == desk.id, Ticket.subject == "Charged twice for November"))
    if busy is not None:
        tools.seen(s, busy, people["carlo"], now - timedelta(seconds=20))
    s.flush()


def view_as_user(s, desk, role):
    """The person View As plays for a role in this demo desk."""
    return s.scalar(select(User).where(User.desk_id == desk.id, User.email == f"{VIEW_AS[role]}@{DOMAIN}"))
