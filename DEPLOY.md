# Deploying NHA

Three pieces: **Supabase** (DB + Auth, already live), the **API** on Render, and
the **frontend** on GitHub Pages. Do them in this order — the frontend build needs
the API's URL.

```
Browser ──reads──▶ Supabase (RLS)
   │   ──writes──▶ Render API ──▶ Supabase
   ▼
GitHub Pages (static React)
```

Reads (standings, rosters, leaders, login) go straight to Supabase and are always
fast. Only writes (lineup save, trades) hit the API.

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

## Notes

- **Custom domain:** set it in Pages settings, add it to `NHA_CORS_ORIGINS`
  (render.yaml) and the Supabase redirect URLs.
- **Private league:** Pages sites are public on the free plan. The *data* is still
  protected by Supabase Auth + RLS (you can't read anything without logging in),
  but the app shell/login page is publicly reachable.
- **Keep-warm (optional later):** if the ~30s cold start becomes annoying, add a
  scheduled workflow that pings `/health` every ~10 min.
