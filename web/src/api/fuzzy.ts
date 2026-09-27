// SPDX-License-Identifier: Apache-2.0

/** A fuzzy score: every query character in order, with bonuses for a prefix, word starts
 *  and runs. null when the query does not match at all. Case-insensitive. */
export function fuzzyScore(query: string, text: string): number | null {
  const q = query.trim().toLowerCase();
  const t = text.toLowerCase();
  if (!q) return 0;
  let score = 0;
  let ti = 0;
  let run = 0;
  for (const ch of q) {
    const found = t.indexOf(ch, ti);
    if (found < 0) return null;
    const wordStart = found === 0 || /[\s\-_./(]/.test(t[found - 1] ?? "");
    run = found === ti ? run + 1 : 0;
    score += 1 + (wordStart ? 3 : 0) + run * 2 - Math.min(found - ti, 5) * 0.2;
    ti = found + 1;
  }
  if (t.startsWith(q)) score += 10;
  return score - t.length * 0.01;
}
