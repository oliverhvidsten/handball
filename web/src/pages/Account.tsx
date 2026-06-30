import { useState } from "react";
import { supabase } from "../lib/supabase";
import { useAuth } from "../auth";
import { Input, Button, Alert } from "../ds";

const MIN_PASSWORD = 8;

const cardStyle: React.CSSProperties = {
  background: "var(--surface-card)",
  border: "1px solid var(--line)",
  borderRadius: "var(--radius-lg)",
  boxShadow: "var(--shadow-sm)",
  padding: "20px 22px",
};

export default function Account() {
  const { session, isCommissioner } = useAuth();
  const email = session?.user.email ?? "";

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const tooShort = next.length > 0 && next.length < MIN_PASSWORD;
  const mismatch = confirm.length > 0 && next !== confirm;
  const canSubmit =
    !busy && current.length > 0 && next.length >= MIN_PASSWORD && next === confirm;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setDone(false);
    if (next !== confirm) { setError("New passwords don't match."); return; }
    if (next.length < MIN_PASSWORD) { setError(`Password must be at least ${MIN_PASSWORD} characters.`); return; }
    setBusy(true);
    try {
      // Verify the current password by re-authenticating. Supabase's updateUser
      // doesn't check the old password, so we confirm identity first.
      const { error: reauth } = await supabase.auth.signInWithPassword({
        email,
        password: current,
      });
      if (reauth) { setError("Current password is incorrect."); return; }

      const { error: upd } = await supabase.auth.updateUser({ password: next });
      if (upd) { setError(upd.message); return; }

      setDone(true);
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update password.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section style={{ maxWidth: 560 }}>
      <h2 style={{ marginBottom: 4 }}>Account</h2>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>
        Manage your account settings.
      </p>

      <div style={{ ...cardStyle, marginTop: 16 }}>
        <h3 style={{ margin: "0 0 12px" }}>Profile</h3>
        <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "8px 16px", margin: 0 }}>
          <dt style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>Email</dt>
          <dd style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--text-body)" }}>{email}</dd>
          <dt style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>Role</dt>
          <dd style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--text-body)" }}>
            {isCommissioner ? "Commissioner" : "Manager"}
          </dd>
        </dl>
      </div>

      <form onSubmit={submit} style={{ ...cardStyle, marginTop: 16, display: "flex", flexDirection: "column", gap: 14 }}>
        <h3 style={{ margin: 0 }}>Change password</h3>

        {error && <Alert tone="error">{error}</Alert>}
        {done && <Alert tone="success">Your password has been updated.</Alert>}

        {/* Hidden username field helps password managers associate the change. */}
        <input type="text" name="username" autoComplete="username" value={email} readOnly hidden />

        <Input
          type="password"
          label="Current password"
          value={current}
          autoComplete="current-password"
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => { setCurrent(e.target.value); setDone(false); }}
        />
        <Input
          type="password"
          label="New password"
          value={next}
          autoComplete="new-password"
          invalid={tooShort}
          hint={`At least ${MIN_PASSWORD} characters.`}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => { setNext(e.target.value); setDone(false); }}
        />
        <Input
          type="password"
          label="Confirm new password"
          value={confirm}
          autoComplete="new-password"
          invalid={mismatch}
          hint={mismatch ? "Passwords don't match." : null}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => { setConfirm(e.target.value); setDone(false); }}
        />

        <div>
          <Button variant="primary" type="submit" disabled={!canSubmit}>
            {busy ? "Updating…" : "Update password"}
          </Button>
        </div>
      </form>
    </section>
  );
}
