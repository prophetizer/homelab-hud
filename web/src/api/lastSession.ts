// SPDX-License-Identifier: Apache-2.0
import type { Me } from "./auth";

/**
 * What this browser remembers about its last sign-in, so the sign-in page can say why it is
 * showing. When a session runs out the browser drops the cookie, and to the server that
 * looks exactly like someone who never signed in; only the browser knows it was. Holds a
 * username and a time, never a credential. A deliberate "Sign out" forgets it.
 */
export interface LastSession {
  subject: string;
  expires: number | null; // epoch seconds
}

export type SignedOut = { kind: "expired"; at: number; subject: string } | { kind: "ended"; subject: string };

const KEY = "hud.lastSession";

export function remember(me: Me): void {
  if (me.source === "forward") return; // the proxy signs you in; HUD's page is never shown
  const value: LastSession = { subject: me.subject, expires: me.session_expires ?? null };
  try {
    localStorage.setItem(KEY, JSON.stringify(value));
  } catch {
    // storage blocked (private window): the page just says less
  }
}

export function recall(): LastSession | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<LastSession>;
    if (typeof v.subject !== "string") return null;
    return { subject: v.subject, expires: typeof v.expires === "number" ? v.expires : null };
  } catch {
    return null;
  }
}

export function forget(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // nothing to forget
  }
}

/** Why a browser that was signed in no longer is: its time ran out, or it was ended early
 *  (a password change, "sign out everywhere", an admin's reset). Null: never signed in here,
 *  or signed out on purpose. */
export function whySignedOut(last: LastSession | null, nowSeconds: number): SignedOut | null {
  if (!last) return null;
  if (last.expires !== null && nowSeconds >= last.expires) {
    return { kind: "expired", at: last.expires, subject: last.subject };
  }
  return { kind: "ended", subject: last.subject };
}
