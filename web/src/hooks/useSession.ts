// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import { type Backends, type Me, fetchBackends, fetchMe, login, logout, register, setup } from "../api/auth";
import { ApiError, UNAUTHORIZED_EVENT, setCsrfToken } from "../api/client";

export type Session =
  | { status: "loading"; backends: Backends | null; me: null; error: string | null }
  | { status: "anonymous"; backends: Backends; me: null; error: string | null }
  | { status: "signed-in"; backends: Backends; me: Me; error: string | null };

export interface SessionApi {
  session: Session;
  signIn: (username: string, password: string) => Promise<void>;
  setUp: (username: string, password: string, displayName: string) => Promise<void>;
  signUp: (username: string, password: string, displayName: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

/**
 * Who am I, and how could I sign in? Resolved once at load, again after every sign-in or
 * sign-out, and whenever any API call answers 401 (a session that expired underneath us).
 */
export function useSession(): SessionApi {
  const [session, setSession] = useState<Session>({ status: "loading", backends: null, me: null, error: null });

  const accept = useCallback((backends: Backends, me: Me) => {
    setCsrfToken(me.csrf_token);
    setSession({ status: "signed-in", backends, me, error: null });
  }, []);

  const refresh = useCallback(async () => {
    let backends: Backends;
    try {
      backends = await fetchBackends();
    } catch (err) {
      setSession({ status: "loading", backends: null, me: null, error: message(err) });
      return;
    }
    try {
      accept(backends, await fetchMe());
    } catch (err) {
      setCsrfToken(null);
      const quiet = err instanceof ApiError && err.status === 401;
      setSession({ status: "anonymous", backends, me: null, error: quiet ? null : message(err) });
    }
  }, [accept]);

  useEffect(() => {
    void refresh();
    const onUnauthorized = () => {
      setCsrfToken(null);
      setSession((prev) => (prev.status === "signed-in" ? { ...prev, status: "anonymous", me: null } : prev));
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [refresh]);

  const withBackends = useCallback(
    async (run: () => Promise<Me>) => {
      const me = await run();
      const backends = session.backends ?? (await fetchBackends());
      accept({ ...backends, setup_required: false }, me);
    },
    [accept, session.backends],
  );

  return {
    session,
    signIn: (u, p) => withBackends(() => login(u, p)),
    setUp: (u, p, d) => withBackends(() => setup(u, p, d)),
    signUp: (u, p, d) => withBackends(() => register(u, p, d)),
    signOut: async () => {
      try {
        await logout();
      } finally {
        setCsrfToken(null);
        await refresh();
      }
    },
  };
}
