import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string;

if (!url || !key) {
  // Surfaced in the console during dev if web/.env.local is missing.
  console.error("Missing VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_KEY");
}

// Reads go directly through this client (RLS-protected). Writes go through the
// FastAPI layer (see lib/api.ts).
export const supabase = createClient(url, key);
