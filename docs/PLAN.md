# Ticket Tock — the Plan

"Every ticket, on time." This is the plan the owner approved on 2026-11-10, with a progress list.

## Progress

- [x] Stage 1 — Foundation
- [ ] Stage 2 — Tickets + workflow
- [ ] Stage 3 — Audit history
- [ ] Stage 4 — SLA & escalation
- [ ] Stage 5 — Agent tools
- [ ] Stage 6 — Customer extras
- [ ] Stage 7 — Insights
- [ ] Stage 8 — Demo, PWA, deploy, README

Changes while building:
- Stage 1: login is by email; status stamp colours are the darker, text-safe (4.5:1) versions of the planned ones.
- The append-only history table (with its hash chain) arrives in stage 2, because ticket moves write to it.
  Stage 3 adds the Audit Log page, the chain check, internal notes and the Inbox.

## Context
A new learning project in `C:\Users\USER\Documents\Workspace\support_ticket_system`, cloned from
https://github.com/jcatibog014/support_ticket_system (it only has a README now). Customers submit
issues; support agents assign, prioritize and resolve them. Skills to practise: **authorization,
state transitions, audit history**. Your choices: the name **Ticket Tock**, the **Ticket stub** look, all
four extra groups (SLA & escalation, Agent tools, Customer extras, Insights & audit tools), and
**all stages in one PR** with a commit per stage.

Package `tickettock`, local port **5010** (DFJC has 5009). Ticket keys look like `TT-42`.

## Stack (same as Plan-It / DFJC)
Flask 3 + SQLAlchemy 2.0 ORM + Alembic, SQLite locally / Neon PostgreSQL live, psycopg 3,
Pillow (attachments → WebP, no EXIF), pytest, gunicorn on Render. Hand-written login (no Flask-Login),
CSRF on every POST, strict CSP (no inline scripts/styles), every page works without JS.
`AGENTS.md` + `CLAUDE.md` pointing to it; README in A1-level English with screenshots.

Copy and adapt from earlier projects (don't re-invent):
- `project_management_board/planit/permissions.py` → the `Access` + plain `can_...()` pattern, one table test.
- `project_management_board/planit/activity.py` → history rows turned into sentences by a template.
- `ecommerce_store/dfjc/orders.py` → `TRANSITIONS` dict + `move()` with a conditional UPDATE
  (`WHERE status = ? AND version = ?`) so two agents can't overwrite each other.
- `auth.py`, `db.py` (Hire Wire's fixed `upgrade_db`, no FK cascade wipe), `dates.py`, `formatting.py`,
  `invites.py`, `mailer.py`/`notices.py` outbox, demo with random `session_version` + `sqlite_autoincrement`
  (avoids the id-reuse cookie bug), `docs/take_screenshots.py`, `docs/make_icons.py`, `tests/conftest.py`.

## Roles (authorization)
| Role | Who | Can |
|---|---|---|
| Customer | open sign-up | submit tickets, see/reply to **only their own**, rate, reopen within 7 days |
| Agent | invite link | see the queue, take/assign to self, reply, internal notes, change status/priority |
| Team Lead | invite link | everything an Agent can + assign to anyone, escalations, merge, saved replies, insights |
| Admin | first account (`flask create-admin`) | everything + staff, categories, SLA rules, business hours, audit log export |

- **`permissions.py` holds every rule**; views/templates only call `can_...()`. `tests/test_permissions.py`
  is a role × action table.
- A customer opening someone else's ticket gets **404** (no leak); a staff action they may not do is **403**.
- **Internal notes are never shown to customers** (filtered in one query helper, tested).
- Every list starts from `tickets.visible_filter(user)`.
- Login: Werkzeug hashes, 5 wrong tries = 15-min lock, recovery code instead of email reset (Hire Wire pattern).

## Status workflow (state transitions) — `workflow.py`
```
New ──> Open ──> In Progress ──> Waiting on Customer ──> In Progress
  │       │           │                  │
  └───────┴───────────┴──────> Resolved ─┴──> Closed (auto after 7 days, or by staff)
Resolved ──customer reopens / replies (≤7 days)──> Open        Any (not Closed) ──> Cancelled (by customer, before work starts)
```
- `TRANSITIONS`, `move_problem()` (pure rules, table test), `move()` (version check + history + notices).
- Rules: a ticket must have an assignee to go In Progress; Resolved needs a resolution note; a customer reply
  on Waiting moves it back to In Progress automatically; Closed is final (a new ticket links to it instead).

## Audit history — `audit.py`
- One append-only `audit_events` row per change: actor, ticket, verb, **field, old value, new value**, time, IP-free.
- No update/delete code paths exist for it; a test fails if any view tries. A deleted user keeps their name in the row.
- **Tamper-evident hash chain:** each row stores `sha256(previous_hash + row data)`; `flask verify-audit` and the
  Audit Log page show "Chain OK" or the first broken row.
- Ticket page shows a timeline (customer view hides internal events); Audit Log page (Admin/Lead) with filters
  (who, what, when, ticket) + CSV export.

## Stages (one PR, a commit per stage)
1. **Foundation** — project setup, models, login/sign-up/recovery code, roles + staff invites, Settings
   (theme, log out, password), Admin setup: categories, priorities, the Ticket stub theme, app shell.
2. **Tickets + workflow** — customer New Ticket form, My Tickets, ticket page with public replies;
   staff Queue (tabs: Unassigned, Mine, All open, Waiting, Resolved), take/assign, priority, `workflow.py` moves.
3. **Audit history** — `audit.py` with field diffs + hash chain, timelines, internal notes, Audit Log page,
   `verify-audit`, in-app Inbox notices (+ optional Gmail outbox email).
4. **SLA & escalation** — SLA rules per priority (first response + resolve), business hours + PH holidays,
   due soon / breached badges, `/tasks/tick` (GitHub Actions cron) escalates breaches to Team Leads.
5. **Agent tools** — saved replies with `{{customer_name}}`, saved views, bulk assign/close, round-robin
   auto-assign per category, "also viewing" warning (short poll), merge duplicates (history kept on both).
6. **Customer extras** — Help Articles (Admin/Lead write; suggested while typing the subject), image
   attachments, CSAT rating after resolve, reopen within 7 days, auto-close after 7 days.
7. **Insights** — server-drawn SVG dashboard (by status, first-response and resolve times, SLA %,
   agent load, CSAT), table view per chart, colours checked with the dataviz validator in light + dark.
8. **Demo, PWA, deploy, README** — per-visitor Try Demo company "Kape Cloud" with sample customers,
   agents and ~30 tickets in every state; **View As** Customer / Agent / Team Lead / Admin; every demo page
   shows an example (`tests/test_demo.py`); PWA; `render.yaml` (`ticket-tock`) + `docs/DEPLOY.md`;
   README screenshots for every page (`tests/test_readme.py`).

## Look: Ticket stub
| Token | Light | Dark |
|---|---|---|
| `--page` | `#fbf7ef` cream paper | `#14171f` |
| `--ink` / sidebar | `#1f2a44` navy | `#1a2236` |
| `--accent` | `#e4572e` coral stamp | `#f0714d` |
| `--muted` | `#6b7280` | `#9aa3b2` |
| Status | New `#3b82f6`, Open `#8b5cf6`, In Progress `#d97706`, Waiting `#64748b`, Resolved `#16a34a`, Closed `#374151`, Cancelled `#9ca3af` | lighter versions, validated |
| Priority | Low, Normal, High, Urgent as arrows (Plan-It style) + Urgent in coral | |

Ticket cards have a dashed "perforation" line and a rubber-stamp status pill; a small clock + ticket logo
(no emoji). Fonts: Bricolage Grotesque (headings) + Inter, self-hosted. Your UI rules: Title Case for names of
pages and headings, sentence case for buttons, aligned equal-height controls, main button last on the right,
theme and Log out in Settings only, sidebar from 56rem, ☰ below.

## Defaults I picked (tell me to change any)
- Customers sign up openly; staff join by a one-time invite link from the Admin.
- No email needed to run: in-app Inbox always; Gmail email is optional (outbox like DFJC).
- One support desk (not multi-company), except demo sandboxes.

## Verification
- `.venv\Scripts\python -m pytest`: permission table, workflow table, audit (diffs, hash chain, no edits),
  internal-note hiding, concurrent move (version conflict), SLA clock with business hours, demo pages non-empty,
  migrations match models, README pictures match.
- Run `flask --app tickettock run --debug --port 5010`, try the demo as each View As role, and check every page
  in light and dark with screenshots before opening the PR.
- `flask --app tickettock verify-audit` reports "Chain OK"; editing a row by hand in SQLite makes it fail.
- Push, confirm `origin` has the commit, open the PR with `gh pr create --body-file` (screenshots as raw URLs).
- Afterwards (your steps): run the tests on an empty Neon branch, go live on Render + Neon.
