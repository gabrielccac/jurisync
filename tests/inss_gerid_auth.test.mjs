import assert from "node:assert/strict";
import test from "node:test";

import { isReusableAuth } from "../scripts/inss_gerid_playwright.mjs";

const jwt = (exp) => ["header", Buffer.from(JSON.stringify({ exp })).toString("base64url"), "signature"].join(".");

test("headless reuse requires a present, unexpired browser credential", () => {
  const now = 2_000_000_000_000;
  assert.equal(isReusableAuth(null, now), false);
  assert.equal(isReusableAuth(jwt(now / 1000 + 60), now), true);
  assert.equal(isReusableAuth(jwt(now / 1000 - 60), now), false);
  assert.equal(isReusableAuth("opaque-token", now), true);
});
