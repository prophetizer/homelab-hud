// SPDX-License-Identifier: Apache-2.0
import { createContext } from "react";

/** Present on a board: a tile's title opens its detail view. Absent inside the detail view
 *  itself, so a detail never opens another. */
export const DetailContext = createContext<((widgetId: string) => void) | null>(null);
