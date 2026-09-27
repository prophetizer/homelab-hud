// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";

/** A row's poster (or, with variant="backdrop", its background art), served by HUD through
 *  the row's own provider (/api/v1/images), so the media server's token never reaches the
 *  browser. Gone without a trace if it fails. */
export function Poster({
  uid,
  large,
  variant = "image",
  className,
}: {
  uid: string;
  large?: boolean;
  variant?: "image" | "backdrop";
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  const q = variant === "backdrop" ? "?variant=backdrop" : "";
  return (
    <img
      className={className ?? (large ? "poster poster--lg" : "poster")}
      src={`/api/v1/images/${encodeURIComponent(uid)}${q}`}
      alt=""
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}
