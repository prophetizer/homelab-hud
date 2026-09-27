// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";

/** A service's icon, from HUD's own /api/v1/icons (never a CDN), or its initial when there
 *  is none or it fails to load. Icons carry brand colour: they identify, never signal
 *  status (invariant 9, amended 2026-09-26). */
export function Icon({
  name,
  title,
  size,
  fallback = "letter",
}: {
  name: string | null | undefined;
  title: string;
  size?: "sm";
  fallback?: "letter" | "none";
}) {
  const [failed, setFailed] = useState(false);
  const cls = size === "sm" ? "icon icon--sm" : "icon";
  if (!name || failed) {
    if (fallback === "none") return null;
    return (
      <span className={`${cls} icon--letter`} aria-hidden="true">
        {title.trim().charAt(0).toUpperCase() || "?"}
      </span>
    );
  }
  return (
    <img
      className={cls}
      data-mono={name.startsWith("mdi-") || undefined}
      src={`/api/v1/icons/${encodeURIComponent(name)}`}
      alt=""
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}
