// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";

/** A service's icon, from HUD's own /api/v1/icons (never a CDN), or its initial when there
 *  is none or it fails to load. Icons carry brand colour: they identify, never signal
 *  status (invariant 9, amended 2026-09-26). */
export function Icon({ name, title }: { name: string | null | undefined; title: string }) {
  const [failed, setFailed] = useState(false);
  if (!name || failed) {
    return (
      <span className="icon icon--letter" aria-hidden="true">
        {title.trim().charAt(0).toUpperCase() || "?"}
      </span>
    );
  }
  return (
    <img
      className="icon"
      data-mono={name.startsWith("mdi-") || undefined}
      src={`/api/v1/icons/${encodeURIComponent(name)}`}
      alt=""
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}
