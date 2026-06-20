"""FastAPI write API for the manager website. Reads stay client-side (direct to
Postgres via Supabase + RLS); writes funnel through here so they reuse the Python
domain rules (validate / apply_arrangement / trade_service) instead of being
reimplemented."""
