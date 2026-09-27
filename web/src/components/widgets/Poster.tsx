// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";

/** A row's poster, served by HUD through the row's own provider (/api/v1/images), so the
 *  media server's token never reaches the browser. Gone without a trace if it fails. */
export function Poster({ uid }: { uid: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <img
      className="poster"
      src={`/api/v1/images/${encodeURIComponent(uid)}`}
      alt=""
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}
