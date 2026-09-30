// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { probeText, startingUrl, type Found, type WizardField } from "./wizard";

const url: WizardField = { name: "SONARR_BASE_URL", kind: "env", hint: "", set_in: null };
const key: WizardField = { name: "sonarr_api_key", kind: "secret", hint: "", set_in: null };

describe("wizard helpers", () => {
  it("prefills a base URL from the found container, and nothing else", () => {
    const found = { suggested_url: "http://sonarr:8989" } as Found;
    expect(startingUrl(url, found)).toBe("http://sonarr:8989");
    expect(startingUrl(url, undefined)).toBe("http://");
    expect(startingUrl(key, found)).toBe("");
  });
  it("says what a test found, or why it failed", () => {
    expect(probeText({ ok: true, seconds: 0.42, resources: 4, sample: ["Sonarr", "a", "b"], error: null })).toBe(
      "Connected in 0.4 s — found 4: Sonarr, a, b, …",
    );
    expect(probeText({ ok: false, seconds: 1, resources: 0, sample: [], error: "HTTP 401" })).toBe("HTTP 401");
  });
});
