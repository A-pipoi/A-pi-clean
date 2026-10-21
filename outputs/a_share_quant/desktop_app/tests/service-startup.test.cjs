const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { serviceEnvironment } = require("../modules/service.cjs");

test("desktop service imports only approved runtime settings from .env", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "a-pi-service-env-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(
    path.join(root, ".env"),
    [
      "TUSHARE_TOKEN='token-value'",
      'A_PI_PUBLIC_ORIGIN="https://127.0.0.1:8000"',
      "UNRELATED_SECRET=must-not-load",
      "MALFORMED",
    ].join("\n"),
  );

  const environment = serviceEnvironment(root);
  assert.equal(environment.TUSHARE_TOKEN, "token-value");
  assert.equal(environment.A_PI_PUBLIC_ORIGIN, "https://127.0.0.1:8000");
  assert.equal(environment.UNRELATED_SECRET, undefined);
});

