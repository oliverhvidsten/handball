# Deploying NHA

Three pieces: **Supabase** (DB + Auth, already live), the **API** on Render, and
the **frontend** on GitHub Pages. Do them in this order — the frontend build needs
the API's URL, and both need the schema to be current.

```
Browser ──reads──▶ Supabase (RLS)
   │   ──writes──▶ Render API ──▶ Supabase
   ▼
GitHub Pages (static React)
```

Most reads (rosters, leaders, login) go straight to Supabase and are always fast.
Writes (lineup save, trades) hit the API — and so do the few reads that can't be
expressed as a Supabase query: **standings** (the ranking's head-to-head step is not
an `ORDER BY`), the **playoff bracket**, and the **free-agency board** (offers are
sealed and redacted per caller).

## 0. Database migrations

The schema is Alembic, in `alembic/versions/`, applied by running it **locally
against the target database** — there is no migration step in the Render deploy.

```bash
python3 -m alembic current        # what the DB is on now
python3 -m alembic upgrade head   # apply everything outstanding
```

> **The default target is production.** `handball/db.py` auto-loads the repo-root
> `.env`, and `HANDBALL_DB_URL` there points at Supabase — so a bare
> `alembic upgrade head` migrates the live database. That is usually what you want
> here; just never run it expecting to hit your local DB.

For the local dev database (what the test suite uses — the Postgres-backed tests
skip unless the URL is localhost):

```bash
docker start handball-pg
HANDBALL_DB_URL="postgresql+psycopg://postgres:dev@localhost:5432/handball_dev" \
  python3 -m alembic upgrade head
```

**Migrate before deploying code that needs the new schema.** The API queries columns
the moment it starts serving; if Render picks up the code first, those endpoints
throw until the migration lands. Every migration in this repo has a working
`downgrade`, so `alembic downgrade <rev>` is a real escape hatch — but check for
rows first, since a downgrade that drops a column drops its data.

## 1. API → Render

1. Push this branch to GitHub (`render.yaml` is at the repo root).
2. Render dashboard → **New → Blueprint** → connect this repo → it reads
   `render.yaml` and creates the `nha-api` service.
3. Set the one secret it asks for: **`HANDBALL_DB_URL`** = your Supabase
   **Session pooler** string, rewritten to `postgresql+psycopg://…`
   (Supabase → Connect → Session pooler; swap the `postgresql://` prefix).
4. Deploy. Note the URL, e.g. `https://nha-api.onrender.com`. Check
   `https://nha-api.onrender.com/health` returns `{"ok":true}`.

> Free tier sleeps after ~15 min idle; the first write after that waits ~30s
> (the Save button shows "Saving…"). Reads/login are unaffected.

## 2. Frontend → GitHub Pages

1. Repo **Settings → Pages → Source → GitHub Actions**.
2. Repo **Settings → Secrets and variables → Actions → Variables** → add three
   **repository variables** (all public-safe):
   - `VITE_SUPABASE_URL` = `https://dabgpgjwvarojclnpptf.supabase.co`
   - `VITE_SUPABASE_PUBLISHABLE_KEY` = your `sb_publishable_…` key
   - `VITE_API_URL` = the Render URL from step 1
3. Merge to `main` (or run the **Deploy web to GitHub Pages** workflow manually).
   It builds `web/` and publishes to **`https://oliverhvidsten.github.io/handball/`**.

## 3. Supabase wiring

- **Authentication → URL Configuration** → add
  `https://oliverhvidsten.github.io/handball` as a Site URL / redirect URL.
- The API already allows the Pages origin via `NHA_CORS_ORIGINS` in `render.yaml`.
  (If you later use a custom domain, add it there and redeploy the API.)

## 4. Add managers

Each manager needs a Supabase **auth user** + a `managers` row + `owner_id` on
their team(s). Use `scripts/seed_owner.py` as the pattern (it sets ownership for a
given auth email). New managers self-serve once you create their auth user and
assign their teams.

## Shipping a change

Sections 0–4 are first-time setup. Day to day it is:

1. **Migrate**, if the change adds one (`alembic upgrade head` — section 0).
2. **Push.** Render redeploys the API from the branch it is watching.
3. **Merge to `main`**, which runs the Pages workflow and publishes the frontend.

Schema first, then API, then frontend — each step is safe to run while the ones
after it are still on the old version, and unsafe in the other order. A database
ahead of the code is harmless (nothing reads the new columns yet); code ahead of
the database is an outage.

Check the seams after: `/health` on the API, and the affected page in the browser.

## Notes

- **Custom domain:** set it in Pages settings, add it to `NHA_CORS_ORIGINS`
  (render.yaml) and the Supabase redirect URLs.
- **Private league:** Pages sites are public on the free plan. The *data* is still
  protected by Supabase Auth + RLS (you can't read anything without logging in),
  but the app shell/login page is publicly reachable.
- **Keep-warm (optional later):** if the ~30s cold start becomes annoying, add a
  scheduled workflow that pings `/health` every ~10 min.
