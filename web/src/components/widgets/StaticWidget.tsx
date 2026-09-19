// SPDX-License-Identifier: Apache-2.0
import type { ResolvedWidget, StaticData } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";

export function StaticWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as StaticData;
  return (
    <WidgetFrame widget={widget}>
      {data.text ? <p className="static__text">{data.text}</p> : null}
      {data.links.length > 0 ? (
        <ul className="static__links">
          {data.links.map((l) => (
            <li key={l.url}>
              <a href={l.url} target="_blank" rel="noreferrer noopener">
                {l.title}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </WidgetFrame>
  );
}
