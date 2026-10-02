// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { whySignedOut } from "./lastSession";

describe("whySignedOut", () => {
  it("says nothing for a browser that never signed in, or signed out on purpose", () => {
    expect(whySignedOut(null, 1_000)).toBeNull();
  });
  it("says the session expired once its time has passed", () => {
    expect(whySignedOut({ subject: "michael", expires: 900 }, 1_000)).toEqual({
      kind: "expired",
      at: 900,
      subject: "michael",
    });
  });
  it("says it was ended early when time was left", () => {
    expect(whySignedOut({ subject: "michael", expires: 2_000 }, 1_000)).toEqual({ kind: "ended", subject: "michael" });
    expect(whySignedOut({ subject: "michael", expires: null }, 1_000)).toEqual({ kind: "ended", subject: "michael" });
  });
});
