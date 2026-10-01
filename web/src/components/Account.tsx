// SPDX-License-Identifier: Apache-2.0
import { type FormEvent, useEffect, useState } from "react";
import {
  type Account as AccountInfo,
  type Me,
  changePassword,
  fetchAccount,
  logoutEverywhere,
  sourceWords,
} from "../api/auth";
import { UNAUTHORIZED_EVENT } from "../api/client";

/** Your account: how you are signed in and until when, your password, and signing out on
 *  every device. Changing the password or signing out everywhere ends this session too, so
 *  both return you to the sign-in page. */
export function Account({ me }: { me: Me }) {
  const [info, setInfo] = useState<AccountInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    fetchAccount(ctl.signal)
      .then(setInfo)
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setError(e instanceof Error ? e.message : String(e));
      });
    return () => ctl.abort();
  }, []);

  const endEverywhere = async () => {
    try {
      await logoutEverywhere();
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <>
      <header className="board__header">
        <h1 className="board__title">Your account</h1>
      </header>
      {error ? (
        <p className="board-status" role="alert">
          {error}
        </p>
      ) : null}
      <div className="account">
        <section className="widget account__card" aria-label="Signed in as">
          <h2 className="account__title">Signed in as</h2>
          <dl className="report__facts">
            <dt>Name</dt>
            <dd>{me.display_name}</dd>
            <dt>Username</dt>
            <dd>
              <code>{me.subject}</code>
            </dd>
            <dt>Signs in with</dt>
            <dd>{sourceWords(me.source)}</dd>
            <dt>Groups</dt>
            <dd>{me.groups.length ? me.groups.join(", ") : "none"}</dd>
            {info?.session_expires ? (
              <>
                <dt>This browser</dt>
                <dd>
                  stays signed in until {new Date(info.session_expires * 1000).toLocaleString()}
                </dd>
              </>
            ) : null}
            {info?.sessions ? (
              <>
                <dt>Signed in on</dt>
                <dd>
                  {info.sessions} browser{info.sessions === 1 ? "" : "s"}
                </dd>
              </>
            ) : null}
          </dl>
          {info?.sessions ? (
            <button className="button" type="button" onClick={() => void endEverywhere()}>
              Sign out everywhere
            </button>
          ) : null}
        </section>
        {info ? (
          info.can_change_password ? (
            <PasswordForm minimum={info.min_password_length} />
          ) : (
            <section className="widget account__card" aria-label="Password">
              <h2 className="account__title">Password</h2>
              <p className="account__note">
                HUD has no password for you: it is kept by{" "}
                {me.source === "oidc" ? "your identity provider" : "the login in front of HUD"}. Change it there.
              </p>
            </section>
          )
        ) : null}
      </div>
    </>
  );
}

function PasswordForm({ minimum }: { minimum: number }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const mismatch = again.length > 0 && again !== next;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== again) return;
    setBusy(true);
    setFailure(null);
    try {
      await changePassword(current, next);
      // Every session ended, this one too: back to the sign-in page with the new password.
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    } catch (err: unknown) {
      setFailure(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <form className="widget account__card" aria-label="Change password" onSubmit={(e) => void submit(e)}>
      <h2 className="account__title">Change password</h2>
      <p className="account__note">
        HUD keeps only a one-way fingerprint of your password and cannot show it to you later — keep it in a
        password manager. Changing it signs you out everywhere, this browser too.
      </p>
      <label className="gate__label">
        Current password
        <input
          className="gate__input"
          type="password"
          autoComplete="current-password"
          required
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
      </label>
      <label className="gate__label">
        New password <span className="gate__optional">(at least {minimum} characters)</span>
        <input
          className="gate__input"
          type="password"
          autoComplete="new-password"
          required
          minLength={minimum}
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
      </label>
      <label className="gate__label">
        New password again
        <input
          className="gate__input"
          type="password"
          autoComplete="new-password"
          required
          value={again}
          onChange={(e) => setAgain(e.target.value)}
          aria-invalid={mismatch || undefined}
        />
      </label>
      {mismatch ? <p className="gate__error">The two new passwords differ.</p> : null}
      {failure ? (
        <p className="gate__error" role="alert">
          {failure}
        </p>
      ) : null}
      <button className="button button--primary" type="submit" disabled={busy || mismatch}>
        {busy ? "Changing…" : "Change password"}
      </button>
    </form>
  );
}
