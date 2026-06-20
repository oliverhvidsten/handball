import React from "react";
import { Input } from "../forms/Input.jsx";
import { Button } from "../forms/Button.jsx";
import { Alert } from "../feedback/Alert.jsx";

/**
 * NHA AuthCard — centered sign-in card. Controlled via email/password/onEmail/
 * onPassword/onSubmit; `error` renders the red callout; `busy` disables the button.
 */
export function AuthCard({
  brand = "NHA",
  title = "National Handball Association",
  subtitle = "Manager sign in",
  email = "",
  password = "",
  onEmail,
  onPassword,
  onSubmit,
  error = null,
  busy = false,
  style = {},
}) {
  const submit = (e) => {
    e.preventDefault();
    onSubmit && onSubmit(email, password);
  };
  return (
    <form
      onSubmit={submit}
      style={{
        width: 360,
        display: "flex",
        flexDirection: "column",
        gap: 14,
        padding: 28,
        background: "var(--surface-card)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-lg)",
        boxShadow: "var(--shadow-lg)",
        ...style,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          width: 38, height: 38, borderRadius: "var(--radius-md)",
          background: "var(--green-500)", color: "var(--ink-950)",
          fontFamily: "var(--font-display)", fontWeight: "var(--weight-black)", fontSize: 17,
        }}>
          ◓
        </span>
        <span style={{
          fontFamily: "var(--font-display)", fontWeight: "var(--weight-black)",
          fontSize: "var(--text-2xl)", letterSpacing: "0.04em", color: "var(--text-heading)",
        }}>
          {brand}
        </span>
      </div>

      <div>
        <h1 style={{ fontSize: "var(--text-lg)", margin: "0 0 2px" }}>{title}</h1>
        <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--muted)" }}>{subtitle}</p>
      </div>

      {error && <Alert tone="error">{error}</Alert>}

      <Input
        type="email"
        label="Email"
        placeholder="you@team.nha"
        value={email}
        autoComplete="email"
        onChange={onEmail ? (e) => onEmail(e.target.value) : undefined}
      />
      <Input
        type="password"
        label="Password"
        placeholder="••••••••"
        value={password}
        invalid={!!error}
        autoComplete="current-password"
        onChange={onPassword ? (e) => onPassword(e.target.value) : undefined}
      />

      <Button variant="primary" size="lg" type="submit" fullWidth disabled={busy || !email || !password}>
        {busy ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}
