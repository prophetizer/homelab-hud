// SPDX-License-Identifier: Apache-2.0
import { Icon } from "./Icon";

/** An empty tile says so kindly: its service's icon, faded, over the tile's own text. */
export function Empty({ icon, text }: { icon: string | null | undefined; text: string }) {
  return (
    <div className="empty">
      <Icon name={icon} title={text} fallback="none" />
      <p className="list__empty">{text}</p>
    </div>
  );
}
