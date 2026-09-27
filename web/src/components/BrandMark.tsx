// SPDX-License-Identifier: Apache-2.0

/** HUD's mark: a dial with its needle. Monochrome (currentColor) — it is identity, and
 *  colour here is reserved for status (invariant 9). */
export function BrandMark() {
  return (
    <svg className="brand__mark" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4.6 17.4a9 9 0 1 1 14.8 0" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M12 13 16.2 8.8" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      <circle cx="12" cy="13" r="1.9" fill="currentColor" />
    </svg>
  );
}
