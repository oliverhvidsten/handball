import { supabase } from "./supabase";

const API_URL = (import.meta.env.VITE_API_URL as string) ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  problems?: string[];
  constructor(status: number, message: string, problems?: string[]) {
    super(message);
    this.status = status;
    this.problems = problems;
  }
}

// The API host (Render free tier) sleeps after ~15min idle; the first request
// after a sleep can get a hard connection failure (browser reports it as
// "Failed to fetch") while the container is still booting, rather than just
// being slow. Retry network-level failures a few times with backoff to ride
// out the boot instead of surfacing a confusing error to the user.
const COLD_START_RETRY_DELAYS_MS = [2000, 4000, 8000];

/**
 * Authenticated call to the FastAPI write layer. Attaches the current Supabase
 * access token as a Bearer header (the server verifies it via JWKS). Throws
 * ApiError on non-2xx, surfacing validation `problems` (e.g. illegal lineup).
 */
export async function apiFetch<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new ApiError(401, "not signed in");

  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    ...(init.headers ?? {}),
  };

  let res: Response;
  let attempt = 0;
  while (true) {
    try {
      res = await fetch(`${API_URL}${path}`, { ...init, headers });
      break;
    } catch (e) {
      if (attempt >= COLD_START_RETRY_DELAYS_MS.length) throw e;
      await new Promise((r) => setTimeout(r, COLD_START_RETRY_DELAYS_MS[attempt]));
      attempt += 1;
    }
  }

  if (!res.ok) {
    let detail: unknown = await res.json().catch(() => null);
    if (detail && typeof detail === "object" && "detail" in detail) {
      detail = (detail as { detail: unknown }).detail;
    }
    if (detail && typeof detail === "object" && "problems" in detail) {
      const problems = (detail as { problems: string[] }).problems;
      throw new ApiError(res.status, problems.join("; "), problems);
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : res.statusText);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/**
 * Authenticated multipart upload (a file field). Same token and error handling as
 * apiFetch, but no Content-Type: the browser sets the multipart boundary itself.
 */
export async function apiUpload<T = unknown>(path: string, form: FormData, method = "PUT"): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new ApiError(401, "not signed in");
  const res = await fetch(`${API_URL}${path}`, { method, body: form, headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) {
    let detail: unknown = await res.json().catch(() => null);
    if (detail && typeof detail === "object" && "detail" in detail) detail = (detail as { detail: unknown }).detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : res.statusText);
  }
  return (await res.json()) as T;
}

/**
 * The URL of a team's logo, or null when it has none (logo_version 0). The version
 * is in the URL so a new upload is a new URL: the API serves the image with a
 * one-year immutable cache header and the browser never shows a stale one. No auth
 * on this route -- an <img> can't send a token, and a logo isn't a secret.
 */
export function teamLogoUrl(slug: string, version: number | null | undefined): string | null {
  if (!version || version <= 0) return null;
  return `${API_URL}/teams/${encodeURIComponent(slug)}/logo?v=${version}`;
}
