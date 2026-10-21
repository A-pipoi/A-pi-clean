const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { X509Certificate } = require("node:crypto");
const { matchesLocalCertificate } = require("../modules/local-certificate.cjs");
const { APP_URL, APP_URL_LOCALHOST } = require("../modules/service.cjs");
const pem = fs.readFileSync(path.join(__dirname, "../../local_tls/localhost.crt.pem"), "utf8");
const fingerprint = new X509Certificate(pem).fingerprint256;

test("desktop accepts only matching current local certificate and HTTPS origins", () => {
  assert.equal(matchesLocalCertificate(APP_URL, pem, fingerprint), true);
  assert.equal(matchesLocalCertificate(APP_URL_LOCALHOST, pem, fingerprint), true);
  for (const url of [
    "http://127.0.0.1:8000/", "https://127.0.0.1:8001/",
    "https://evil.test/", "https://user@localhost:8000/",
  ]) assert.equal(matchesLocalCertificate(url, pem, fingerprint), false);
  assert.equal(matchesLocalCertificate(APP_URL, pem, "changed-certificate"), false);
  assert.equal(matchesLocalCertificate(APP_URL, "malformed", fingerprint), false);
  assert.equal(matchesLocalCertificate(APP_URL, pem, ""), false);
});
