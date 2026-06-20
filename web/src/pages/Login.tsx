import { useState } from "react";
import { useAuth } from "../auth";
import { AuthCard } from "../ds";

export default function Login() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: string, p: string) => {
    setError(null);
    setBusy(true);
    try {
      await signIn(e, p);
    } catch (err) {
      setError(err instanceof Error ? err.message : "sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="center">
      <AuthCard
        email={email}
        password={password}
        onEmail={setEmail}
        onPassword={setPassword}
        onSubmit={submit}
        error={error}
        busy={busy}
      />
    </div>
  );
}
