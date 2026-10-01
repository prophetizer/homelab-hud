// SPDX-License-Identifier: Apache-2.0
import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  type GroupInfo,
  type Me,
  type UserOut,
  createUser,
  deleteUser,
  fetchGroups,
  fetchUsers,
  updateUser,
} from "../api/auth";
import { formatAge } from "../api/format";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Accounts and what they may do (admins only). Groups are the ones rbac.yaml defines; a
 *  person who signs in through the proxy or single sign-on gets their groups from there,
 *  so those are shown, not edited. Every change here is in the audit log. */
export function Users({ me }: { me: Me }) {
  const [users, setUsers] = useState<UserOut[] | null>(null);
  const [groups, setGroups] = useState<GroupInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    Promise.all([fetchUsers(), fetchGroups()])
      .then(([u, g]) => {
        setUsers(u.users);
        setGroups(g.groups);
        setError(null);
      })
      .catch((e: unknown) => setError(message(e)));
  }, []);
  useEffect(load, [load]);

  return (
    <>
      <header className="board__header">
        <h1 className="board__title">Users</h1>
        <span className="board__meta">groups and what they may do are defined in /config/rbac.yaml</span>
      </header>
      {error ? (
        <p className="board-status" role="alert">
          {error}
        </p>
      ) : null}
      <div className="account">
        <section className="widget account__card account__card--wide" aria-label="Accounts">
          <h2 className="account__title">Accounts</h2>
          <ul className="users">
            {users?.map((u) => (
              <UserRow key={u.id} user={u} groups={groups} self={u.subject === me.subject && u.source === me.source} onChanged={load} />
            ))}
          </ul>
        </section>
        <NewUserForm groups={groups} onCreated={load} />
      </div>
    </>
  );
}

function GroupPicker({ groups, value, onChange }: { groups: GroupInfo[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset className="users__groups">
      <legend className="gate__label">Groups</legend>
      {groups.map((g) => (
        <label key={g.name} className="users__group">
          <input
            type="checkbox"
            checked={value.includes(g.name)}
            onChange={(e) => onChange(e.target.checked ? [...value, g.name] : value.filter((x) => x !== g.name))}
          />
          {g.name}
          <span className="gate__optional">
            {g.admin
              ? "everything"
              : g.permissions.length === 0
                ? "nothing"
                : `${g.permissions.length} permission${g.permissions.length === 1 ? "" : "s"}`}
          </span>
        </label>
      ))}
    </fieldset>
  );
}

type Mode = "view" | "groups" | "password" | "remove";

function UserRow({ user, groups, self, onChanged }: { user: UserOut; groups: GroupInfo[]; self: boolean; onChanged: () => void }) {
  const [mode, setMode] = useState<Mode>("view");
  const [picked, setPicked] = useState<string[]>(user.groups);
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const local = user.source === "local";

  const act = async (run: () => Promise<unknown>, said: string) => {
    setFailure(null);
    try {
      await run();
      setDone(said);
      setMode("view");
      setPassword("");
      onChanged();
    } catch (e: unknown) {
      setFailure(message(e));
    }
  };

  return (
    <li className="users__row">
      <div className="users__who">
        <span className="users__name">
          {user.display_name}
          {self ? <span className="gate__optional"> (you)</span> : null}
        </span>
        <span className="users__meta">
          <code>{user.subject}</code> · {local ? "HUD account" : user.source === "oidc" ? "single sign-on" : "proxy login"} ·{" "}
          {user.groups.length ? user.groups.join(", ") : "no groups"} ·{" "}
          {user.last_seen ? `last seen ${formatAge(new Date(user.last_seen * 1000).toISOString())}` : "never signed in"}
        </span>
      </div>
      {mode === "view" ? (
        <div className="users__actions">
          {local ? (
            <button className="board__edit" type="button" onClick={() => setMode("groups")}>
              Groups
            </button>
          ) : null}
          {local ? (
            <button className="board__edit" type="button" onClick={() => setMode("password")}>
              Set password
            </button>
          ) : null}
          {!self ? (
            <button className="board__edit" type="button" onClick={() => setMode("remove")}>
              Remove
            </button>
          ) : null}
        </div>
      ) : null}
      {mode === "groups" ? (
        <form
          className="users__edit"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            void act(() => updateUser(user.id, { groups: picked }), "Groups saved.");
          }}
        >
          <GroupPicker groups={groups} value={picked} onChange={setPicked} />
          <div className="users__actions">
            <button className="button button--primary" type="submit">
              Save groups
            </button>
            <button className="button" type="button" onClick={() => setMode("view")}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {mode === "password" ? (
        <form
          className="users__edit"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            void act(
              () => updateUser(user.id, { password }),
              self ? "Password set; sign in again." : "Password set; they are signed out everywhere.",
            );
          }}
        >
          <label className="gate__label">
            New password for {user.subject}
            <input
              className="gate__input"
              type="password"
              autoComplete="new-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <p className="account__note">Tell them the new password yourself; HUD sends nothing. They are signed out everywhere.</p>
          <div className="users__actions">
            <button className="button button--primary" type="submit">
              Set password
            </button>
            <button className="button" type="button" onClick={() => setMode("view")}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {mode === "remove" ? (
        <div className="users__edit" role="group" aria-label={`Remove ${user.subject}`}>
          <p className="account__note">
            Remove <code>{user.subject}</code>? They are signed out and cannot sign in again
            {local ? "" : " until the proxy or identity provider lets them in, which recreates the account"}.
          </p>
          <div className="users__actions">
            <button className="button" type="button" onClick={() => void act(() => deleteUser(user.id), "Removed.")}>
              Remove
            </button>
            <button className="button" type="button" onClick={() => setMode("view")}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
      {failure ? (
        <p className="gate__error" role="alert">
          {failure}
        </p>
      ) : null}
      {done && mode === "view" ? (
        <p className="account__note" role="status">
          {done}
        </p>
      ) : null}
    </li>
  );
}

function NewUserForm({ groups, onCreated }: { groups: GroupInfo[]; onCreated: () => void }) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setFailure(null);
    try {
      const u = await createUser({ username, password, display_name: displayName || null, groups: picked });
      setDone(`Added ${u.subject}. Tell them their password yourself; HUD sends nothing.`);
      setUsername("");
      setDisplayName("");
      setPassword("");
      setPicked([]);
      onCreated();
    } catch (err: unknown) {
      setFailure(message(err));
    }
  };

  return (
    <form className="widget account__card" aria-label="Add a user" onSubmit={(e) => void submit(e)}>
      <h2 className="account__title">Add a user</h2>
      <label className="gate__label">
        Username
        <input className="gate__input" autoCapitalize="none" autoComplete="off" required value={username} onChange={(e) => setUsername(e.target.value)} />
      </label>
      <label className="gate__label">
        <span>
          Display name <span className="gate__optional">(optional)</span>
        </span>
        <input className="gate__input" autoComplete="off" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </label>
      <label className="gate__label">
        Password
        <input className="gate__input" type="password" autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      <GroupPicker groups={groups} value={picked} onChange={setPicked} />
      {picked.length === 0 ? (
        <p className="account__note">With no group they get only what rbac.yaml gives everyone else.</p>
      ) : null}
      {failure ? (
        <p className="gate__error" role="alert">
          {failure}
        </p>
      ) : null}
      {done ? (
        <p className="account__note" role="status">
          {done}
        </p>
      ) : null}
      <button className="button button--primary" type="submit">
        Add user
      </button>
    </form>
  );
}
