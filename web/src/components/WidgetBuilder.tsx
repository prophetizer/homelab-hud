// SPDX-License-Identifier: Apache-2.0
import { useEffect, useMemo, useState } from "react";
import {
  type Group,
  type Options,
  type Selection,
  type WidgetType,
  TYPES,
  addWidget,
  build,
  changes,
  defaults,
  editWidget,
  fetchCatalog,
  fetchWidgetYaml,
  fromWidget,
  previewWidget,
  removeWidget,
  typesFor,
  unmanaged,
} from "../api/builder";
import { ApiError } from "../api/client";
import type { ResolvedBoard, ResolvedWidget } from "../api/types";
import { Widget } from "./Widget";

export type BuilderMode = { kind: "add"; section: string | null } | { kind: "edit"; widget: string };

const STYLES: Partial<Record<WidgetType, [string, string][]>> = {
  metric: [["number", "Number"], ["gauge", "Gauge"]],
  resource: [["lead", "Lead number"], ["readings", "Readings"], ["hero", "Hero strip"], ["fields", "Fields"]],
  chart: [["line", "Line"], ["area", "Area"]],
  list: [["rows", "Rows"], ["cards", "Cards"], ["media", "Media"], ["shelf", "Poster shelf"], ["services", "Service tiles"], ["grid", "Squares"]],
  status: [["wall", "Wall"], ["headline", "Headline"]],
};
const RANGES: Partial<Record<WidgetType, [string, string][]>> = {
  metric: [["", "No trend"], ["1h", "1 hour"], ["6h", "6 hours"], ["24h", "24 hours"]],
  resource: [["", "No trend"], ["1h", "1 hour"], ["6h", "6 hours"], ["24h", "24 hours"]],
  chart: [["1h", "1 hour"], ["6h", "6 hours"], ["24h", "24 hours"], ["7d", "7 days"]],
  uptime: [["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"]],
  capacity: [["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"]],
};

/** A group's resources to list: a few by default; with a search, the ones it matches (all of
 *  them when the search names the group itself). */
function matching(g: Group, query: string): Group["resources"] {
  const q = query.trim().toLowerCase();
  if (!q) return g.resources.slice(0, 6);
  if (`${g.provider} ${g.kind}`.includes(q)) return g.resources;
  return g.resources.filter((r) => r.name.toLowerCase().includes(q));
}

/** The widget builder (PLAN §8.5 Flow B): pick what to show, how, and its options, with a
 *  live preview; it writes the board's YAML, keeping everything else in the file as it was. */
export function WidgetBuilder({
  board,
  mode,
  onDone,
  onClose,
}: {
  board: ResolvedBoard;
  mode: BuilderMode;
  onDone: (fresh: ResolvedBoard) => void;
  onClose: () => void;
}) {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState<Selection | null>(null);
  const [type, setType] = useState<WidgetType | null>(null);
  const [options, setOptions] = useState<Options | null>(null);
  const [before, setBefore] = useState<Record<string, unknown> | null>(null); // edit: the widget as written
  const [revision, setRevision] = useState(board.revision);
  const [yamlOnly, setYamlOnly] = useState(false); // edit: a shape the builder does not write
  const [preview, setPreview] = useState<ResolvedWidget | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const ctl = new AbortController();
    fetchCatalog(ctl.signal)
      .then((c) => setGroups(c.groups))
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setProblem(e instanceof Error ? e.message : String(e));
      });
    return () => ctl.abort();
  }, []);

  useEffect(() => {
    if (mode.kind !== "edit") return;
    fetchWidgetYaml(board.name, mode.widget)
      .then(({ widget, revision: rev }) => {
        setBefore(widget);
        setRevision(rev);
        const read = fromWidget(widget);
        if (!read) {
          setYamlOnly(true);
          return;
        }
        setSel(read.sel);
        setType(read.type);
        setOptions(read.options);
      })
      .catch((e: unknown) => setProblem(e instanceof Error ? e.message : String(e)));
  }, [board.name, mode]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const group = sel && groups ? groups.find((g) => g.provider === sel.provider && g.kind === sel.kind) : undefined;
  const draft = useMemo(() => (type && sel && options ? build(type, sel, options) : null), [type, sel, options]);

  // The preview follows the form, a moment after the last change.
  useEffect(() => {
    if (!draft) return;
    let live = true;
    const t = window.setTimeout(() => {
      previewWidget(board.name, draft)
        .then((w) => {
          if (live) {
            setPreview(w);
            setProblem(null);
          }
        })
        .catch((e: unknown) => {
          if (live) setProblem(e instanceof Error ? e.message : String(e));
        });
    }, 350);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [board.name, draft]);

  const pick = (s: Selection, g: Group) => {
    setSel(s);
    const first = typesFor(s, g)[0] ?? null;
    setType(first);
    setOptions(first ? { ...defaults(first, s, g), section: mode.kind === "add" ? mode.section : null } : null);
  };
  const choose = (t: WidgetType) => {
    if (!sel || !group) return;
    setType(t);
    setOptions((o) => ({ ...defaults(t, sel, group), section: o?.section ?? null, title: o?.title ?? "" }));
  };
  const set = (patch: Partial<Options>) => setOptions((o) => (o ? { ...o, ...patch } : o));

  const save = async () => {
    if (!draft || !type) return;
    setBusy(true);
    setProblem(null);
    try {
      const fresh =
        mode.kind === "add"
          ? await addWidget(board.name, revision, draft)
          : await editWidget(board.name, mode.widget, revision, changes(type, before ?? {}, draft));
      onDone(fresh);
    } catch (e) {
      setProblem(
        e instanceof ApiError && e.status === 409
          ? "The board changed since you opened it. Close this, reload the board, and try again."
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (mode.kind !== "edit" || !window.confirm(`Remove “${mode.widget}” from ${board.title}?`)) return;
    setBusy(true);
    try {
      onDone(await removeWidget(board.name, mode.widget, revision));
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const shown = (groups ?? []).filter((g) => {
    const q = query.trim().toLowerCase();
    return !q || `${g.provider} ${g.kind}`.includes(q) || g.resources.some((r) => r.name.toLowerCase().includes(q));
  });
  const metricChoices = group ? Object.entries(group.metrics).filter(([, u]) => type !== "capacity" || u === "pct") : [];
  const extra = before && type ? unmanaged(type, before) : [];

  return (
    <aside className="builder" role="dialog" aria-label={mode.kind === "add" ? "Add a widget" : `Edit ${mode.widget}`}>
      <header className="builder__head">
        <h2>{mode.kind === "add" ? "Add a widget" : `Edit ${mode.widget}`}</h2>
        <button type="button" className="detail__close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </header>

      {yamlOnly ? (
        <div className="builder__body">
          <p className="connect__result">
            This widget uses options the builder doesn't write. Edit it in the board's YAML; you can still remove it here.
          </p>
          <button type="button" className="button" disabled={busy} onClick={() => void remove()}>
            Remove widget
          </button>
        </div>
      ) : (
        <div className="builder__body">
          {mode.kind === "add" ? (
            <section className="builder__step">
              <h3>1 · What to show</h3>
              <input
                className="builder__search"
                type="search"
                placeholder="Search providers, kinds and names"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              {groups === null ? <p className="connect__empty">Loading what HUD collects…</p> : null}
              <ul className="builder__groups">
                {shown.map((g) => (
                  <li key={`${g.provider}:${g.kind}`} className="builder__group">
                    <button
                      type="button"
                      className="builder__pick"
                      aria-pressed={sel?.provider === g.provider && sel.kind === g.kind && !sel.uid}
                      onClick={() => pick({ provider: g.provider, kind: g.kind }, g)}
                    >
                      <b>
                        {g.provider} · {g.kind}
                      </b>
                      <span>{g.count === 1 ? "1 resource" : `all ${g.count}`}</span>
                    </button>
                    {matching(g, query).map((r) => (
                      <button
                        key={r.uid}
                        type="button"
                        className="builder__pick builder__pick--one"
                        aria-pressed={sel?.uid === r.uid}
                        onClick={() => pick({ provider: g.provider, kind: g.kind, uid: r.uid }, g)}
                      >
                        <span className="status-dot" data-state={r.state} />
                        {r.name}
                      </button>
                    ))}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {sel && group && type && options ? (
            <>
              <section className="builder__step">
                <h3>{mode.kind === "add" ? "2 · How" : "Widget"}</h3>
                <div className="builder__types">
                  {typesFor(sel, group).map((t) => {
                    const meta = TYPES.find((x) => x.type === t);
                    return (
                      <button key={t} type="button" className="builder__type" aria-pressed={t === type} onClick={() => choose(t)} disabled={mode.kind === "edit"}>
                        <b>{meta?.label}</b>
                        <span>{meta?.hint}</span>
                      </button>
                    );
                  })}
                </div>
              </section>

              <section className="builder__step">
                <h3>{mode.kind === "add" ? "3 · Options" : "Options"}</h3>
                <div className="builder__form">
                  <label className="connect__field">
                    <span>Title</span>
                    <input value={options.title} onChange={(e) => set({ title: e.target.value })} />
                  </label>
                  {(board.sections ?? []).length > 0 ? (
                    <label className="connect__field">
                      <span>Section</span>
                      <select value={options.section ?? ""} onChange={(e) => set({ section: e.target.value || null })}>
                        <option value="">Above the sections</option>
                        {(board.sections ?? []).map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.title}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  {["metric", "chart", "bars", "capacity", "resource"].includes(type) && metricChoices.length > 0 ? (
                    <label className="connect__field">
                      <span>{type === "resource" ? "Lead reading" : "Reading"}</span>
                      <select value={options.metric ?? ""} onChange={(e) => set({ metric: e.target.value })}>
                        {metricChoices.map(([m, u]) => (
                          <option key={m} value={m}>
                            {m.replace(/_/g, " ")}
                            {u ? ` (${u})` : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  {type === "resource" ? (
                    <fieldset className="builder__checks">
                      <legend>Other readings</legend>
                      {metricChoices
                        .filter(([m]) => m !== options.metric)
                        .map(([m]) => (
                          <label key={m}>
                            <input
                              type="checkbox"
                              checked={options.stats.includes(m)}
                              onChange={(e) =>
                                set({ stats: e.target.checked ? [...options.stats, m] : options.stats.filter((x) => x !== m) })
                              }
                            />
                            {m.replace(/_/g, " ")}
                          </label>
                        ))}
                    </fieldset>
                  ) : null}
                  {STYLES[type] ? (
                    <label className="connect__field">
                      <span>Style</span>
                      <select value={options.style} onChange={(e) => set({ style: e.target.value })}>
                        {STYLES[type]!.map(([v, l]) => (
                          <option key={v} value={v}>
                            {l}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  {RANGES[type] ? (
                    <label className="connect__field">
                      <span>{type === "capacity" ? "Growth measured over" : type === "uptime" || type === "chart" ? "Over" : "Trend"}</span>
                      <select value={options.range} onChange={(e) => set({ range: e.target.value })}>
                        {RANGES[type]!.map(([v, l]) => (
                          <option key={v} value={v}>
                            {l}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  {type === "bars" ? (
                    <label className="connect__field">
                      <span>How many</span>
                      <input type="number" min={1} max={50} value={options.limit} onChange={(e) => set({ limit: Number(e.target.value) || 8 })} />
                    </label>
                  ) : null}
                  <div className="builder__size">
                    <label className="connect__field">
                      <span>Width</span>
                      <select value={options.w} onChange={(e) => set({ w: Number(e.target.value) })}>
                        {[1, 2, 3, 4].map((n) => (
                          <option key={n} value={n}>
                            {n} column{n > 1 ? "s" : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="connect__field">
                      <span>Height</span>
                      <select value={options.h} onChange={(e) => set({ h: Number(e.target.value) })}>
                        {[1, 2, 3].map((n) => (
                          <option key={n} value={n}>
                            {n} row{n > 1 ? "s" : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  {extra.length > 0 ? (
                    <p className="connect__note">Also set in YAML and kept as written: {extra.join(", ")}</p>
                  ) : null}
                </div>
              </section>

              <section className="builder__step">
                <h3>Preview</h3>
                <div className="builder__preview">
                  {preview ? <Widget widget={preview} board={board.name} /> : <p className="connect__empty">Rendering…</p>}
                </div>
              </section>
            </>
          ) : null}

          {problem ? (
            <p className="connect__result" role="alert">
              {problem}
            </p>
          ) : null}
        </div>
      )}

      {!yamlOnly ? (
        <footer className="builder__foot">
          {mode.kind === "edit" ? (
            <button type="button" className="button" disabled={busy} onClick={() => void remove()}>
              Remove
            </button>
          ) : null}
          <span className="builder__spacer" />
          <button type="button" className="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="button button--primary" disabled={busy || !draft} onClick={() => void save()}>
            {busy ? "Saving…" : mode.kind === "add" ? "Add to board" : "Save changes"}
          </button>
        </footer>
      ) : null}
    </aside>
  );
}
