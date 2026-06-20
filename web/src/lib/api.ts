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

/**
 * Authenticated call to the FastAPI write layer. Attaches the current Supabase
 * access token as a Bearer header (the server verifies it via JWKS). Throws
 * ApiError on non-2xx, surfacing validation `problems` (e.g. illegal lineup).
 */
export async function apiFetch<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new ApiError(401, "not signed in");

  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(init.headers ?? {}),
    },
  });

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
