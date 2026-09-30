// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import {
  type CatalogEntry,
  type Connection,
  type Found,
  type ProbeResult,
  connectService,
  fetchCatalog,
  fetchFound,
  probeText,
  startingUrl,
  testConnection,
} from "../api/wizard";
import { Icon } from "./widgets/Icon";

/** Connect a service (PLAN §8.5, round 3): what runs on this server that HUD knows how to
 *  read, and every bundled service; pick one, enter its address and credential, test, save.
 *  What it writes is an ordinary provider file — the credential goes to the secret store. */
export function Connect() {
  const [catalog, setCatalog] = useState<CatalogEntry[] | null>(null);
  const [found, setFound] = useState<Found[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<{ entry: CatalogEntry; found?: Found } | null>(null);
  const load = useCallback(() => {
    const ctl = new AbortController();
    fetchCatalog(ctl.signal)
      .then((c) => setCatalog(c.templates))
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setError(e instanceof Error ? e.message : String(e));
      });
    fetchFound(ctl.signal)
      .then((f) => setFound(f.found))
      .catch(() => {
        if (!ctl.signal.aborted) setFound([]); // no Docker provider: the catalog still works
      });
    return () => ctl.abort();
  }, []);
  useEffect(load, [load]);
  const entry = (name: string) => catalog?.find((c) => c.name === name);
  const waiting = found.filter((f) => !f.connected);

  return (
    <div className="connect">
      <header className="board__header">
        <h1 className="board__title">Connect a service</h1>
        <span className="board__meta">writes /config/providers/&lt;name&gt;.yaml · credentials to the secret store</span>
      </header>
      {error ? (
        <p className="board-status" role="alert">
          {error}
        </p>
      ) : null}

      <section className="board-section" aria-label="Found on this server">
        <div className="board-section__head">
          <h2 className="board-section__title">Found on this server</h2>
          <span className="board-section__rule" />
          <span className="board-section__sum">
            {found.length === 0 ? "no matching containers" : `${waiting.length} not connected yet · ${found.length - waiting.length} connected`}
          </span>
        </div>
        {found.length === 0 ? (
          <p className="connect__empty">
            Nothing found. Discovery needs the Docker provider; every bundled service is still listed below.
          </p>
        ) : (
          <ul className="connect__grid">
            {found.map((f) => {
              const e = entry(f.template);
              return (
                <li key={f.container} className="connect__card" data-connected={f.connected || undefined}>
                  <span className="widget__icon">
                    <Icon name={e?.icon ?? null} title={f.service} size="sm" />
                  </span>
                  <span className="connect__what">
                    <b>{f.service}</b>
                    <span className="connect__sub" title={f.image}>
                      container {f.container}
                    </span>
                    {f.note ? <span className="connect__note">{f.note}</span> : null}
                  </span>
                  {f.connected ? (
                    <span className="connect__done">connected</span>
                  ) : e ? (
                    <button type="button" className="button button--primary" onClick={() => setOpen({ entry: e, found: f })}>
                      Connect
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="board-section" aria-label="Every service HUD knows">
        <div className="board-section__head">
          <h2 className="board-section__title">Every service HUD knows</h2>
          <span className="board-section__rule" />
          <span className="board-section__sum">{catalog ? `${catalog.length} bundled` : "loading…"}</span>
        </div>
        <ul className="connect__grid">
          {(catalog ?? []).map((c) => (
            <li key={c.name} className="connect__card" data-connected={c.connected || undefined}>
              <span className="widget__icon">
                <Icon name={c.icon ?? c.name} title={c.service} size="sm" />
              </span>
              <span className="connect__what">
                <b>{c.service}</b>
                {c.docs ? (
                  <a className="connect__sub" href={c.docs} target="_blank" rel="noreferrer noopener">
                    API docs ↗
                  </a>
                ) : null}
              </span>
              {c.connected ? (
                <span className="connect__done">connected</span>
              ) : (
                <button type="button" className="button" onClick={() => setOpen({ entry: c })}>
                  Connect
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>

      {open ? (
        <ConnectDialog
          entry={open.entry}
          found={open.found}
          onClose={() => setOpen(null)}
          onConnected={() => {
            setOpen(null);
            load();
          }}
        />
      ) : null}
    </div>
  );
}

function ConnectDialog({
  entry,
  found,
  onClose,
  onConnected,
}: {
  entry: CatalogEntry;
  found: Found | undefined;
  onClose: () => void;
  onConnected: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(entry.fields.filter((f) => f.kind === "env").map((f) => [f.name, startingUrl(f, found)])),
  );
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<string[] | null>(null);
  const body = (): Connection => ({ template: entry.name, values, secrets });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const act = async (what: "test" | "save") => {
    setBusy(what);
    setProblem(null);
    try {
      if (what === "test") {
        setProbe(await testConnection(body()));
      } else {
        const r = await connectService(body());
        setDone(r.warnings);
      }
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  const edited = () => setProbe(null); // a change invalidates the last test

  return (
    <div className="detail" role="dialog" aria-modal="true" aria-label={`Connect ${entry.service}`} onClick={onClose}>
      <form
        className="detail__panel connect__dialog"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          void act(probe?.ok ? "save" : "test");
        }}
      >
        <button type="button" className="detail__close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2 className="widget__title">
          <span className="widget__icon">
            <Icon name={entry.icon ?? entry.name} title={entry.service} size="sm" />
          </span>
          Connect {entry.service}
        </h2>
        {done ? (
          <div className="connect__result" data-ok="">
            <p>
              <b>{entry.service} is connected.</b> Its data appears within a minute. To show it on a board, add a widget
              to that board's YAML (the bundled template's comments list its resources).
            </p>
            {done.map((w) => (
              <p key={w} className="connect__note">
                {w}
              </p>
            ))}
            <button type="button" className="button button--primary" onClick={onConnected}>
              Done
            </button>
          </div>
        ) : (
          <>
            {entry.fields.map((f) =>
              f.kind === "env" ? (
                <label key={f.name} className="connect__field">
                  <span>{f.name.endsWith("_URL") ? "Address" : f.name}</span>
                  <input
                    type={f.name.endsWith("_URL") ? "url" : "text"}
                    required
                    value={values[f.name] ?? ""}
                    onChange={(e) => {
                      setValues((v) => ({ ...v, [f.name]: e.target.value }));
                      edited();
                    }}
                    spellCheck={false}
                    autoComplete="off"
                  />
                  <small>{f.hint}</small>
                </label>
              ) : (
                <label key={f.name} className="connect__field">
                  <span>{f.hint}</span>
                  <input
                    type="password"
                    required={!f.set_in}
                    value={secrets[f.name] ?? ""}
                    placeholder={f.set_in ? `already set in ${f.set_in} — leave empty to keep it` : ""}
                    onChange={(e) => {
                      setSecrets((s) => ({ ...s, [f.name]: e.target.value }));
                      edited();
                    }}
                    autoComplete="new-password"
                  />
                  <small>
                    Stored as <code>{f.name}</code> in /config/secrets.yaml (owner-only; keep it out of git). The provider
                    file only names it.
                  </small>
                </label>
              ),
            )}
            {probe ? (
              <p className="connect__result" data-ok={probe.ok || undefined} role="status">
                {probeText(probe)}
              </p>
            ) : null}
            {problem ? (
              <p className="connect__result" role="alert">
                {problem}
              </p>
            ) : null}
            <div className="connect__actions">
              <button type="button" className="button" disabled={busy !== null} onClick={() => void act("test")}>
                {busy === "test" ? "Testing…" : "Test connection"}
              </button>
              <button
                type="button"
                className="button button--primary"
                disabled={busy !== null || !probe?.ok}
                title={probe?.ok ? undefined : "Test the connection first"}
                onClick={() => void act("save")}
              >
                {busy === "save" ? "Connecting…" : "Connect"}
              </button>
            </div>
          </>
        )}
      </form>
    </div>
  );
}
