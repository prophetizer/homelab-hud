// SPDX-License-Identifier: Apache-2.0
import type { ResolvedWidget } from "../api/types";
import { BarsWidget } from "./widgets/BarsWidget";
import { EmbedWidget } from "./widgets/EmbedWidget";
import { ListWidget } from "./widgets/ListWidget";
import { MetricWidget } from "./widgets/MetricWidget";
import { ResourceWidget } from "./widgets/ResourceWidget";
import { StaticWidget } from "./widgets/StaticWidget";
import { UnsupportedWidget } from "./widgets/UnsupportedWidget";

export function Widget({ widget, board }: { widget: ResolvedWidget; board?: string | undefined }) {
  switch (widget.type) {
    case "static":
      return <StaticWidget widget={widget} />;
    case "resource":
      return <ResourceWidget widget={widget} />;
    case "list":
      return <ListWidget widget={widget} />;
    case "metric":
      return <MetricWidget widget={widget} />;
    case "bars":
      return <BarsWidget widget={widget} />;
    case "embed":
      return <EmbedWidget widget={widget} board={board} />;
    default:
      return <UnsupportedWidget widget={widget} />;
  }
}
