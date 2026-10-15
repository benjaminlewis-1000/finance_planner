# finance_planner

Self-hosted retirement planning dashboard. Pulls current account balances from
a Tiller Google Sheet, combines them with household assumptions (salary
steps, expense changes, one-off events, FERS pension), and runs the
[Owl](https://github.com/mdlacasse/Owl) retirement optimizer to produce and
compare scenarios.

See `.claude` plan history / project notes for the full build plan and design
decisions.

## Development

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# One-time: grant read-only access to the Tiller Google Sheet
python backend/scripts/authorize_tiller.py

# Run the backend
cd backend && uvicorn app.main:app --reload
```

Or via Docker:

```
docker compose -f docker/docker-compose.yml up --build
```
