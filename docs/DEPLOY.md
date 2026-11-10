# Put Ticket Tock on the Internet

Ticket Tock runs on **Render** (the website) and **Neon** (the database). Both have a free plan.
Do the steps in this order. It takes about 20 minutes.

## 1. Make the Database (Neon)

1. Log in at https://neon.tech.
2. Press **New Project**. Name: `ticket-tock`. Region: **Asia Pacific (Singapore)**.
3. Open **Connection Details** and copy the **connection string**. It starts with `postgresql://`.
   Keep it secret: it holds the database password.

## 2. Make the Website (Render)

1. Log in at https://render.com with your GitHub account.
2. Press **New**, then **Blueprint**, and choose the `support_ticket_system` repo.
3. Render reads `render.yaml` and asks for some values:
   - `DATABASE_URL`: paste the Neon connection string.
   - `MAIL_USERNAME` and `MAIL_PASSWORD`: leave them empty for now (see step 5).
4. Press **Apply**. Wait for **Live** (about 5 minutes).
   Render makes `SECRET_KEY` and `TICK_KEY` by itself.

## 3. Make the First Admin

Render's free plan has no Shell, so do this from your computer, against Neon:

```
$env:DATABASE_URL = "postgresql://...your Neon string..."
.venv\Scripts\flask --app tickettock upgrade-db
.venv\Scripts\flask --app tickettock create-admin
```

It asks for your name, email and a password, and shows your **recovery code** once. Save it.
Then close the terminal, so the Neon string is not kept.

Open your site, log in, and invite your team from **Staff**.

## 4. Start the Timer (GitHub Actions)

The timer escalates late tickets, closes old resolved tickets and sends emails.

1. On Render, open the service, then **Environment**, and copy the value of `TICK_KEY`.
2. On GitHub, open the repo, then **Settings**, **Secrets and variables**, **Actions**.
3. Add two secrets:
   - `TICK_URL`: `https://<your-site>.onrender.com/tasks/tick`
   - `TICK_KEY`: the value you copied.
4. Open **Actions**, then **Desk timer**, and press **Run workflow** once to test it.

Without the timer, Ticket Tock still works: opening Home or the Queue runs it too.

## 5. Email (Optional)

Everything is always in each person's Inbox. To also send emails:

1. In your Google account, turn on 2-Step Verification, then make an **App password**.
2. On Render, set `MAIL_USERNAME` (the Gmail address) and `MAIL_PASSWORD` (the app password).

## Check It

- Run the tests on an **empty** Neon branch once, to check the PostgreSQL side:
  `$env:TEST_DATABASE_URL = "postgresql://...a test branch..."` then `.venv\Scripts\python -m pytest`.
  Never use the live database for this: every test deletes all tables.
- After a merge, check that Render shows the new commit as **Live**.
- `flask --app tickettock verify-audit` (with the Neon `DATABASE_URL`) says "Chain OK" for the desk.
