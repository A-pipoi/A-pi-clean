import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  createSecurityApi,
  securityDictionary,
  securityText,
} from "../dist/account-security.mjs";
import { createAuthClient } from "../dist/auth-client.mjs";

test("all four locales have complete dictionaries and OTP error translations", () => {
  const keys = Object.keys(securityDictionary["en-US"]).sort();
  for (const [locale, dictionary] of Object.entries(securityDictionary)) {
    assert.deepEqual(Object.keys(dictionary).sort(), keys);
    for (const key of keys) assert.ok(securityText(locale, key));
    if (locale === "en-US" || locale === "fr-FR")
      for (const value of Object.values(dictionary))
        assert.doesNotMatch(value, /[\u4e00-\u9fff]/);
  }
  assert.equal(
    securityText("unknown", "title"),
    securityDictionary["en-US"].title,
  );
});

test("two-factor controls use explicit open and close actions for the current state", () => {
  const source = readFileSync(new URL("../dist/account-security.mjs", import.meta.url), "utf8");
  for (const [locale, open, close] of [
    ["zh-CN", "打开双重验证", "关闭双重验证"],
    ["en-US", "Turn on two-factor authentication", "Turn off two-factor authentication"],
    ["fr-FR", "Activer l’authentification à deux facteurs", "Désactiver l’authentification à deux facteurs"],
    ["ja-JP", "二要素認証を有効にする", "二要素認証を無効にする"],
  ]) {
    assert.equal(securityDictionary[locale].openTwoFactor, open);
    assert.equal(securityDictionary[locale].closeTwoFactor, close);
  }
  assert.match(source, /openButton\.disabled = factor\.value\.enabled/);
  assert.match(source, /closeButton\.disabled = !factor\.value\.enabled/);
  assert.doesNotMatch(source, /t\(factor\.value\.enabled \? "enabled" : "disabled"\)/);
  const twoFactor = source.slice(
    source.indexOf('const twoFactor = section("twoFactor")'),
    source.indexOf('if (session.role === "admin")'),
  );
  assert.match(twoFactor, /const credentials = \[\];/);
  assert.doesNotMatch(twoFactor, /options\.(?:forget|logout)\(\)/);
  assert.match(twoFactor, /\[\{ key: "otp", label: "otp" \}\]/);
});

test("security API sends contract payloads and handles 204 without parsing JSON", async () => {
  const calls = [];
  const api = createSecurityApi(async (path, init) => {
    calls.push([path, init]);
    return init.method === "GET"
      ? new Response(JSON.stringify({ sessions: [] }))
      : new Response(null, { status: 204 });
  });
  assert.deepEqual(await api.request("/api/auth/sessions"), { sessions: [] });
  assert.equal(calls[0][1].body, undefined);
  for (const [path, body] of [
    ["/api/auth/revoke-session", { jti: "mock-session" }],
    ["/api/auth/revoke-all", {}],
    [
      "/api/auth/change-password",
      { old_password: "mock-old", new_password: "mock-new", otp: "mock-code" },
    ],
    ["/api/auth/2fa/setup", { current_password: "mock-password" }],
    [
      "/api/auth/2fa/enable",
      { current_password: "mock-password", otp: "mock-code" },
    ],
    [
      "/api/auth/2fa/disable",
      { current_password: "mock-password", otp: "mock-code" },
    ],
    [
      "/api/admin/users",
      {
        username: "mock-user",
        password: "mock-password",
        role: "researcher",
        current_password: "mock-current",
      },
    ],
    [
      "/api/admin/users/action",
      {
        username: "mock-user",
        action: "role",
        role: "admin",
        current_password: "mock-current",
      },
    ],
  ]) {
    assert.deepEqual(await api.request(path, body), {});
    const [actual, init] = calls.at(-1);
    assert.equal(actual, path);
    assert.deepEqual(JSON.parse(init.body), body);
    assert.equal(init.credentials, "same-origin");
    assert.equal(init.cache, "no-store");
  }
});

test("403 wrong current password stays inline; 401 retains global expiry behavior", async () => {
  let status = 200,
    expired = 0;
  const client = createAuthClient({
    origin: "http://localhost",
    onExpired() {
      expired++;
    },
    fetch: async () =>
      new Response(
        JSON.stringify({
          authenticated: true,
          username: "mock-user",
          role: "admin",
          expires_at: "2030-01-01",
          error: "invalid_credentials",
        }),
        { status },
      ),
  });
  await client.boot();
  const api = createSecurityApi(client.fetch);
  status = 403;
  await assert.rejects(api.request("/api/admin/users", {}), /forbidden/);
  assert.ok(client.current());
  assert.equal(expired, 0);
  status = 401;
  await assert.rejects(api.request("/api/auth/sessions"), /expired/);
  assert.equal(expired, 1);
  assert.equal(client.current(), null);
});

test("OTP and recovery codes pass through optional fourth login argument", async () => {
  const calls = [];
  const identity = {
    authenticated: true,
    username: "mock-user",
    role: "researcher",
    expires_at: "2030-01-01",
  };
  const client = createAuthClient({
    origin: "http://localhost",
    onExpired() {},
    fetch: async (path, init) => {
      calls.push([path, init]);
      return new Response(JSON.stringify(identity));
    },
  });
  await client.login("mock-user", "mock-password", false, "mock-recovery");
  assert.equal(JSON.parse(calls[0][1].body).otp, "mock-recovery");
  calls.length = 0;
  await client.login("mock-user", "mock-password", false);
  assert.equal("otp" in JSON.parse(calls[0][1].body), false);
});

test("required and invalid OTP errors survive login and security requests", async () => {
  for (const error of ["totp_required", "totp_invalid"]) {
    const fetcher = async () =>
      new Response(JSON.stringify({ error }), { status: 403 });
    await assert.rejects(
      createSecurityApi(fetcher).request("/api/auth/2fa/enable", {}),
      new RegExp(error),
    );
    const client = createAuthClient({
      origin: "http://localhost",
      onExpired() {},
      fetch: fetcher,
    });
    await assert.rejects(
      client.login("mock-user", "mock-password", false),
      new RegExp(error),
    );
  }
});
