import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth";
import { apiFetch, apiUpload, ApiError, teamLogoUrl } from "../lib/api";
import { Alert, Button, EmptyState, Input, Toast } from "../ds";

// GET/PUT /teams/{slug}/settings (handball/team_settings.py). The city (`name`,
// `slug`) is a key across the league and is not editable here; everything on this
// page is additive display identity.
interface TeamSettings {
  slug: string;
  city: string;
  nickname: string | null;
  abbr: string;
  abbr_is_custom: boolean;
  display_name: string;
  logo_version: number;
  has_logo: boolean;
}

const ACCEPT = "image/png,image/jpeg,image/webp,image/gif";

export default function Settings() {
  const { activeTeam, teams, refreshTeams } = useAuth();
  const owned = !!activeTeam && teams.some((t) => t.slug === activeTeam.slug);

  const [settings, setSettings] = useState<TeamSettings | null>(null);
  const [nickname, setNickname] = useState("");
  const [abbr, setAbbr] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = async (slug: string) => {
    const s = await apiFetch<TeamSettings>(`/teams/${slug}/settings`, { method: "GET" });
    setSettings(s);
    setNickname(s.nickname ?? "");
    setAbbr(s.abbr_is_custom ? s.abbr : "");
  };

  useEffect(() => {
    setErr(null);
    setSettings(null);
    if (activeTeam) load(activeTeam.slug).catch((e) => setErr(e instanceof Error ? e.message : "could not load"));
  }, [activeTeam?.slug]);

  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "action failed");

  async function save() {
    if (!activeTeam) return;
    setErr(null);
    setBusy("save");
    try {
      const s = await apiFetch<TeamSettings>(`/teams/${activeTeam.slug}/settings`, {
        method: "PUT",
        body: JSON.stringify({ nickname: nickname.trim() || null, abbr: abbr.trim() || null }),
      });
      setSettings(s);
      setNickname(s.nickname ?? "");
      setAbbr(s.abbr_is_custom ? s.abbr : "");
      await refreshTeams();
      setToast("Saved.");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  async function upload(file: File) {
    if (!activeTeam) return;
    setErr(null);
    setBusy("logo");
    try {
      const form = new FormData();
      form.append("file", file);
      const s = await apiUpload<TeamSettings>(`/teams/${activeTeam.slug}/logo`, form);
      setSettings(s);
      await refreshTeams();
      setToast("Logo updated.");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function removeLogo() {
    if (!activeTeam) return;
    setErr(null);
    setBusy("logo");
    try {
      const s = await apiFetch<TeamSettings>(`/teams/${activeTeam.slug}/logo`, { method: "DELETE" });
      setSettings(s);
      await refreshTeams();
      setToast("Logo removed.");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  if (!activeTeam) {
    return (
      <section>
        <h2 style={{ marginBottom: 16 }}>Team settings</h2>
        <EmptyState title="No team selected" message="Pick one of your teams in the switcher to edit its name, mark and logo." />
      </section>
    );
  }

  const logo = settings ? teamLogoUrl(settings.slug, settings.logo_version) : null;
  const dirty = settings != null && (
    (nickname.trim() || null) !== (settings.nickname ?? null) ||
    (abbr.trim().toUpperCase() || null) !== (settings.abbr_is_custom ? settings.abbr : null)
  );
  const card: React.CSSProperties = {
    background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)",
    padding: 18, marginBottom: 18,
  };
  const previewName = `${activeTeam.name}${nickname.trim() ? ` ${nickname.trim()}` : ""}`;
  const previewAbbr = (abbr.trim() || settings?.abbr || activeTeam.abbr).toUpperCase();

  return (
    <section>
      <h2 style={{ marginBottom: 4 }}>Team settings</h2>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>{activeTeam.name}</p>

      {!owned && (
        <Alert tone="warning" style={{ marginBottom: 14 }}>You don't own this team, so nothing here can be saved.</Alert>
      )}
      {err && <Alert tone="error" style={{ marginBottom: 14 }}>{err}</Alert>}

      {/* Preview: how the mark and name will read in the switcher and on the Teams page. */}
      <div style={{ ...card, display: "flex", alignItems: "center", gap: 14 }}>
        <span style={{
          flex: "none", width: 56, height: 56, borderRadius: "var(--radius-md)", overflow: "hidden",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          background: "var(--ink-900)", color: "#fff",
          fontFamily: "var(--font-display)", fontWeight: "var(--weight-black)", fontSize: "var(--text-lg)",
        }}>
          {logo
            ? <img src={logo} alt="" width={56} height={56} style={{ width: "100%", height: "100%", objectFit: "contain", display: "block", background: "#fff" }} />
            : previewAbbr}
        </span>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: "var(--font-display)", fontWeight: "var(--weight-bold)", fontSize: "var(--text-xl)", lineHeight: 1.15 }}>
            {previewName}
          </div>
          <div style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>Preview · this is how it appears across the league</div>
        </div>
      </div>

      <div style={card}>
        <h3 style={{ margin: "0 0 12px" }}>Name and abbreviation</h3>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
          <Input
            label="Team name"
            placeholder="e.g. Aces"
            value={nickname}
            maxLength={30}
            disabled={!owned || busy != null}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNickname(e.target.value)}
          />
          <Input
            label="Abbreviation"
            placeholder={`${settings?.abbr ?? activeTeam.abbr} (derived)`}
            value={abbr}
            maxLength={4}
            disabled={!owned || busy != null}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setAbbr(e.target.value.toUpperCase())}
          />
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <Button variant="primary" disabled={!owned || !dirty || busy != null} onClick={() => void save()}>
            {busy === "save" ? "Saving…" : "Save"}
          </Button>
          {dirty && settings && (
            <Button disabled={busy != null} onClick={() => { setNickname(settings.nickname ?? ""); setAbbr(settings.abbr_is_custom ? settings.abbr : ""); }}>
              Reset
            </Button>
          )}
        </div>
      </div>

      <div style={card}>
        <h3 style={{ margin: "0 0 6px" }}>Logo</h3>
        <p style={{ color: "var(--muted)", fontSize: "var(--text-sm)", margin: "0 0 12px" }}>
          PNG, JPEG, WebP or GIF up to 2 MB. Will be resized to 256x256.
        </p>
        <input
          ref={fileRef}
          type="file"
          accept={ACCEPT}
          disabled={!owned || busy != null}
          style={{ display: "none" }}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }}
        />
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Button variant="primary" disabled={!owned || busy != null} onClick={() => fileRef.current?.click()}>
            {busy === "logo" ? "Uploading…" : settings?.has_logo ? "Replace logo" : "Upload logo"}
          </Button>
          {settings?.has_logo && (
            <Button disabled={!owned || busy != null} onClick={() => void removeLogo()}>Remove logo</Button>
          )}
        </div>
      </div>

      {toast && <Toast tone="success" title={toast} onClose={() => setToast(null)} />}
    </section>
  );
}
