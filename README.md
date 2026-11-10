# Ticket Tock

**Every ticket, on time.**

Ticket Tock is a help desk website. Customers send a ticket when something goes wrong.
The support team takes the ticket, sets how urgent it is, and fixes it.

This project is being built in stages. The plan is in [docs/PLAN.md](docs/PLAN.md).
Pictures of every page come with the last stage.

## Run It on Your Computer

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\flask --app tickettock upgrade-db
.venv\Scripts\flask --app tickettock create-admin
.venv\Scripts\flask --app tickettock run --debug --port 5010
```

Then open http://127.0.0.1:5010.

## Test It

```
.venv\Scripts\python -m pytest
```
