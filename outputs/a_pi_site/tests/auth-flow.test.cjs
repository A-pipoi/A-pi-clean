const test = require("node:test");
const assert = require("node:assert/strict");
const modulePromise = import("../dist/auth-client.mjs");
const session = { authenticated: true, username: "alice", role: "admin", expires_at: "2030-01-01T00:00:00Z" };

test('forced-change session retains server restriction flag without expiring on 403', async () => {
  const {createAuthClient} = await modulePromise;
  const client = createAuthClient({origin:'http://localhost:8000',onExpired(){assert.fail();},fetch:async(input)=>
    input === '/api/auth/session' ? new Response(JSON.stringify({...session,must_change_password:true})) : new Response(JSON.stringify({error:'password_change_required'}),{status:403})});
  assert.equal((await client.boot()).must_change_password,true);
  assert.equal((await client.fetch('/api/workspace')).status,403);
  assert.equal(client.current().must_change_password,true);
});

test("session 200 is the only authority; POST injects CSRF header", async () => {
  const { createAuthClient } = await modulePromise;
  const calls = [];
  const client = createAuthClient({ origin: "http://localhost:8000", onExpired() {}, fetch: async (input, init) => {
    calls.push([input, init]);
    return new Response(JSON.stringify(session));
  } });
  assert.equal((await client.boot()).username, "alice");
  await client.fetch("/api/paper/order", { method: "POST", headers: { "Custom": "kept" } });
  assert.equal(calls[1][1].headers.get("X-A-Pi-Request"), "1");
  assert.equal(calls[1][1].headers.get("Custom"), "kept");
  assert.equal(calls[1][1].credentials, "same-origin");
});

test("session 401 stays signed out; protected traffic never reaches the network", async () => {
  const { createAuthClient } = await modulePromise;
  let calls = 0;
  const client = createAuthClient({ origin: "http://localhost:8000", onExpired() { assert.fail(); }, fetch: async () => { calls++; return new Response("{}", { status: 401 }); } });
  assert.equal(await client.boot(), null);
  assert.equal((await client.fetch("/api/workspace")).status, 401);
  assert.equal(calls, 1);
});

test("global 401 clears identity and notifies once", async () => {
  const { createAuthClient } = await modulePromise;
  let expired = 0;
  const client = createAuthClient({ origin: "http://localhost:8000", onExpired() { expired++; }, fetch: async (input) => new Response(JSON.stringify(session), { status: input === "/api/auth/session" ? 200 : 401 }) });
  await client.boot();
  await client.fetch("/api/workspace");
  await client.fetch("/api/paper/state");
  assert.equal(expired, 1);
  assert.equal(client.current(), null);
});

test("external requests neither gain CSRF headers nor expire local sessions", async () => {
  const { createAuthClient } = await modulePromise;
  let options;
  const client = createAuthClient({ origin: "http://localhost:8000", onExpired() { assert.fail(); }, fetch: async (_input, init) => { options = init; return new Response("{}", { status: 401 }); } });
  await client.fetch("https://elsewhere.invalid/api/test", { method: "POST" });
  assert.equal(options.headers, undefined);
});

test("Request headers and method survive wrapper", async () => {
  const { createAuthClient } = await modulePromise;
  let sent;
  const client = createAuthClient({ origin: "http://localhost:8000", onExpired() {}, fetch: async (_input, init) => { sent = init; return new Response(JSON.stringify(session)); } });
  await client.boot();
  await client.fetch(new Request("http://localhost:8000/api/test", { method: "POST", headers: { "Kept": "yes" }, body: "{}" }));
  assert.equal(sent.headers.get("Kept"), "yes");
  assert.equal(sent.headers.get("X-A-Pi-Request"), "1");
});

test("logout clears local identity even on network failure", async () => {
  const { createAuthClient } = await modulePromise;
  const client = createAuthClient({ origin: "http://localhost:8000", onExpired() {}, fetch: async (input) => { if (input === "/api/auth/logout") throw new Error("offline"); return new Response(JSON.stringify(session)); } });
  await client.boot();
  await assert.rejects(client.logout(), /offline/);
  assert.equal(client.current(), null);
});
