# Ticket Tock

**Every ticket, on time.**

Ticket Tock is a help desk website. A customer sends a **ticket** when something goes wrong.
The support team takes the ticket, sets how urgent it is, talks with the customer, and solves it.
Every change is written in a **history** that nobody can change.

I made this app to practise **authorization** (who may do what), **state transitions**
(how a ticket moves from New to Closed) and **audit history** (a true record of every change).

![The queue](docs/screenshots/queue.png)

## Try It

Press **Try demo** on the login page. You get your own sample help desk, "Kape Cloud",
a made-up coffee subscription app. Nobody else sees your demo, and it is deleted after 24 hours.

You start as **Ben Torres, a Team Lead**. Use **View As** (at the bottom of the menu)
to be someone else:

| View As | Person | What you see |
|---|---|---|
| Customer | Maria Santos | Her own tickets, the Help Articles, New Ticket |
| Agent | Ana Reyes | The queue, her tickets, saved replies |
| Team Lead | Ben Torres | Everything an agent sees, plus Insights and the Audit Log |
| Admin | Sam Rivera | Everything, plus Staff, Categories and Time Limits |

A short note says you are in the demo, then goes away by itself. After that, a small
**Demo** tag by the logo and **Sign up for real** at the bottom of the menu stay.

| Log in | The demo starts |
|---|---|
| ![Log in](docs/screenshots/login.png) | ![The demo's welcome note](docs/screenshots/demo-start.png) |

## What It Can Do

### Roles

| Role | Who | What they can do |
|---|---|---|
| **Customer** | Signs up on the site | Send tickets, see **only their own**, reply, rate the help |
| **Agent** | Joins with an invite link | Take tickets, reply, write internal notes, move **their own** tickets |
| **Team Lead** | Joins with an invite link | Like an agent, plus give any ticket to anyone, merge, change many at once, Insights, Audit Log |
| **Admin** | The first one is made on the command line | Everything, plus staff, roles, categories, working hours and time limits |

- Every rule is in one file, `tickettock/permissions.py`, and the tests check every role against every action.
- A customer who opens someone else's ticket gets "not found", so ticket numbers never leak.
- An admin can't change their own role, so the desk always keeps an admin.

| Staff | Settings |
|---|---|
| ![Staff](docs/screenshots/staff.png) | ![Settings](docs/screenshots/settings.png) |

### The Workflow

A ticket moves through these steps:

**New** -> **Open** -> **In Progress** <-> **Waiting on Customer** -> **Resolved** -> **Closed**

- Taking a new ticket opens it. To start work or wait for the customer, the ticket needs someone on it.
- To resolve a ticket, you write **how it was solved**. The customer sees it.
- When the customer replies, a Waiting ticket goes back to In Progress **by itself**.
- A customer can **reopen** a resolved ticket for 7 days. After that, it closes by itself.
- A customer can cancel a ticket before work starts. Closed and cancelled tickets are final.
- Two people can't overwrite each other: every change checks the ticket's **version**. A form from an old page is refused.

| Ticket | Ticket (dark) |
|---|---|
| ![Ticket](docs/screenshots/ticket.png) | ![Ticket in dark mode](docs/screenshots/ticket-dark.png) |

### History You Can Trust

- Every change is one **history line**: who, when, what, the old value and the new value.
- The code only **adds** lines. It never changes or deletes them (a test checks the code).
- Each line has a **hash** that includes the line before it, like a chain. If someone edits a line in the
  database, the chain breaks, and the Audit Log shows it. `flask --app tickettock verify-audit` checks it too.
- Team Leads see the **Audit Log** with filters. Admins can download it as a CSV file.
- Customers see the history of their own tickets, without the team's internal lines.

| Ticket history | Audit Log |
|---|---|
| ![Ticket history](docs/screenshots/ticket-history.png) | ![Audit Log](docs/screenshots/audit-log.png) |

### Time Limits (SLA)

- Each priority has two limits: the **first reply** and **solved**. For example, Urgent: 1 hour and 4 hours.
- The clocks run only in **working hours** (Monday to Friday, 8 AM to 5 PM, Philippine time) and skip **holidays**.
- Waiting on the customer **pauses** the solved clock.
- Each ticket shows "due in 45 min", "due soon" or "2 h late".
- Late tickets are **escalated**: the Team Leads get a notice, and the ticket shows in the **Overdue** tab.
  An agent can also ask a Team Lead for help.
- A timer runs every 15 minutes (GitHub Actions), and also when staff open Home or the queue.

| Escalated ticket | Overdue tab | Time Limits |
|---|---|---|
| ![Escalated](docs/screenshots/escalated.png) | ![Overdue](docs/screenshots/queue-overdue.png) | ![Time limits](docs/screenshots/time-limits.png) |

### Agent Tools

- **Saved replies** with `{name}`, `{agent}` and `{ticket}` filled in.
- **Search and Filter**: search by ticket number, subject or customer. One **Filter** menu holds the category and priority.
- **Saved views**: save your queue filters with a name.
- **Bulk changes** for Team Leads: tick tickets, then give them to someone, set the priority, or close them.
- **Round-robin**: tickets in a category can go to the agents in turn.
- **"Also looking"**: see when a teammate has the same ticket open.
- **Merge** a duplicate ticket into another one from the same customer.
- **Internal notes** that only the team sees.

| Home | Saved replies | Inbox |
|---|---|---|
| ![Home](docs/screenshots/home.png) | ![Saved replies](docs/screenshots/saved-replies.png) | ![Inbox](docs/screenshots/inbox.png) |

| Search and Filter |
|---|
| ![The Filter menu](docs/screenshots/queue-filter.png) |

### For Customers

- **My Tickets** shows open and closed tickets.
- **Help Articles** are open to everyone. While you type a new ticket's subject, matching articles show up.
- Add up to 3 **pictures** to a ticket or a reply. Camera details (like the place) are removed.
- **Rate** the help from 1 to 5 stars after a ticket is solved.
- The **Inbox** tells you about replies and changes. Email is optional.

| My Tickets | New ticket | A ticket with a picture |
|---|---|---|
| ![My Tickets](docs/screenshots/my-tickets.png) | ![New ticket](docs/screenshots/new-ticket.png) | ![Customer ticket](docs/screenshots/customer-ticket.png) |

| Rating | Help Articles (visitors) | Search |
|---|---|---|
| ![Rating](docs/screenshots/rating.png) | ![Help](docs/screenshots/help.png) | ![Help search](docs/screenshots/help-search.png) |

| A Help Article |
|---|
| ![Help article](docs/screenshots/help-article.png) |

### Insights

Team Leads see charts for the last 7, 30 or 90 days: tickets sent and solved, open tickets by status,
each person's load, the median time to reply and to solve, the time limits met, and the ratings.
Every chart can open as a table.

| Insights | Insights (dark) |
|---|---|
| ![Insights](docs/screenshots/insights.png) | ![Insights in dark mode](docs/screenshots/insights-dark.png) |

### Categories, Phone and App

- Admins keep the list of **categories**, and can turn on round-robin for each one.
- Every page works on a phone. You can **install** Ticket Tock like an app.
- Dark mode is in **Settings**.

| Categories | Phone: queue | Phone: ticket |
|---|---|---|
| ![Categories](docs/screenshots/categories.png) | ![Phone queue](docs/screenshots/phone-queue.png) | ![Phone ticket](docs/screenshots/phone-ticket.png) |

## Tools

| Tool | What it is | What I use it for |
|---|---|---|
| **Python** | A programming language | All the code on the server |
| **Flask** | A small web framework for Python | Pages, forms and links |
| **SQLAlchemy** | A tool to use a database from Python | The tables: desks, users, tickets, history ... |
| **Alembic** | Database migrations | Changing the tables without losing data |
| **SQLite** | A small database in one file | The database on my computer and in tests |
| **PostgreSQL (Neon)** | A big database in the cloud | The database on the live site |
| **Jinja** | Templates for HTML | Making each page |
| **HTML, CSS, JavaScript** | The languages of web pages | The look, and small extras (every page also works without JavaScript) |
| **SVG** | Pictures made from code | The charts and the icons |
| **Pillow** | A picture library | Checking pictures and making small WebP copies |
| **markdown-it-py + nh3** | Markdown and safe HTML | Help Articles that can't run scripts |
| **pytest** | A testing tool | Over 300 tests, for example for every role and every move |
| **Playwright** | A browser robot | The screenshots and the app icon |
| **GitHub Actions** | Jobs that run on a schedule | The timer for time limits (every 15 minutes) |
| **Render** | A hosting platform | Running the live site |

## Run It on Your Computer

You need Python 3.13.

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\flask --app tickettock upgrade-db
.venv\Scripts\flask --app tickettock create-admin
.venv\Scripts\flask --app tickettock run --debug --port 5010
```

Then open http://127.0.0.1:5010 and log in, or press **Try demo**.

Run the tests:

```
.venv\Scripts\python -m pytest
```

To put it on the internet, follow [docs/DEPLOY.md](docs/DEPLOY.md). The plan is in [docs/PLAN.md](docs/PLAN.md).
