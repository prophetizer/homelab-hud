// SPDX-License-Identifier: Apache-2.0
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/base.css";
import { applyStoredTheme, watchThemePark } from "./theme";

applyStoredTheme();
watchThemePark();

const root = document.getElementById("root");
if (!root) {
  throw new Error("#root missing from index.html");
}
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
