// SPDX-License-Identifier: Apache-2.0
import { type FormEvent, useState } from "react";
import { BrandMark } from "./BrandMark";
import { type Backends, oidcStartUrl } from "../api/auth";

interface Props {
  backends: Backends;
  error: string | null;
  onSignIn: (username: string, password: string) => Promise<void>;
  onSetUp: (username: string, password: string, displayName: string) => Promise<void>;
  onSignUp: (username: string, password: string, displayName: string) => Promise<void>;
}

type Mode = "setup" | "login" | "register";

/**
 * The gate. First run shows account creation (PLAN.md §14.2 — no default credentials);
 * afterwards whichever of local / OIDC is enabled. A forward-auth-only deployment cannot
 * sign in here at all, and says so instead of showing a form that cannot work.
 */
export function Login({ backends, error, onSignIn, onSetUp, onSignUp }: Props) {
  const local = backends.backends.includes("local");
  const oidc = backends.backends.includes("oidc");
  const [mode, setMode] = useState<Mode>(backends.setup_required ? "setup" : "login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const next = window.location.pathname + window.location.search;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setFailure(null);
    try {
      if (mode === "setup") await onSetUp(username, password, displayName);
      else if (mode === "register") await onSignUp(username, password, displayName);
      else await onSignIn(username, password);
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const title = mode === "setup" ? "Create the admin account" : mode === "register" ? "Create an account" : "Sign in";

  return (
    <div className="gate">
      <form className="gate__card" onSubmit={submit} aria-labelledby="gate-title">
        <div className="sidebar__brand">
          <BrandMark />
          HUD
        </div>
        <h1 id="gate-title" className="gate__title">
          {title}
        </h1>
        {mode === "setup" ? (
          <p className="gate__hint">
            This is a fresh install. The first account is placed in the <code>admins</code> group.
          </p>
        ) : null}
        {local ? (
          <>
            <label className="gate__label">
              Username
              <input
                className="gate__input"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            {mode !== "login" ? (
              <label className="gate__label">
                <span>
                  Display name <span className="gate__optional">(optional)</span>
                </span>
                <input
                  className="gate__input"
                  name="display_name"
                  autoComplete="name"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                />
              </label>
            ) : null}
            <label className="gate__label">
              Password
              <input
                className="gate__input"
                name="password"
                type="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                required
                minLength={mode === "login" ? 1 : 12}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {failure ? (
              <p className="gate__error" role="alert">
                {failure}
              </p>
            ) : null}
            <button className="button button--primary" type="submit" disabled={busy}>
              {busy ? "…" : title}
            </button>
            {mode === "login" && backends.registration ? (
              <button className="gate__link" type="button" onClick={() => setMode("register")}>
                Need an account? Register
              </button>
            ) : null}
            {mode === "register" ? (
              <button className="gate__link" type="button" onClick={() => setMode("login")}>
                Have an account? Sign in
              </button>
            ) : null}
          </>
        ) : null}
        {oidc && mode !== "setup" ? (
          <a className="button" href={oidcStartUrl(next)}>
            {local ? "Sign in with single sign-on" : "Continue with single sign-on"}
          </a>
        ) : null}
        {!local && !oidc ? (
          <p className="gate__hint">
            Sign-in is handled by the proxy in front of HUD (<code>auth.backends: [forward]</code>). Reach
            this page through the proxy, or enable <code>local</code> in settings.yaml as a break-glass path.
          </p>
        ) : null}
        {error ? (
          <p className="gate__error" role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </div>
  );
}
