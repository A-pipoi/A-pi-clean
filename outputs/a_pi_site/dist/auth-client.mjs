/** @typedef {{authenticated:true,username:string,role:string,expires_at:string,must_change_password?:boolean}} Session */

/** Server-authoritative sessions; no credentials or tokens enter browser storage.
 * @param {{fetch:typeof fetch, origin:string, onExpired:(reason?:string)=>void, activityTarget?:EventTarget}} options
 */
export function createAuthClient(options) {
  /** @type {Session|null} */
  let session = null;
  let generation = 0;
  let activityPending = false;
  const activityTarget =
    options.activityTarget ||
    (typeof document === "undefined" ? null : document);
  /** @param {Event} event */
  function recordActivity(event) {
    if (event.isTrusted && session) activityPending = true;
  }
  for (const type of ["pointerdown", "keydown", "wheel"])
    activityTarget?.addEventListener(type, recordActivity, {
      capture: true,
      passive: true,
    });
  const rawFetch = options.fetch;
  const origin = new URL(options.origin).origin;

  function forget() {
    session = null;
    generation += 1;
    activityPending = false;
  }

  /** @type {typeof fetch} */
  async function authenticatedFetch(input, init = {}) {
    const request = input instanceof Request ? input : null;
    const url = new URL(request ? request.url : String(input), origin);
    if (url.origin !== origin || !url.pathname.startsWith("/api/"))
      return rawFetch(input, init);
    const method = (init.method || request?.method || "GET").toUpperCase();
    const authentication = url.pathname.startsWith("/api/auth/");
    const publicEndpoint =
      (["/api/auth/login", "/api/auth/register"].includes(url.pathname) &&
        method === "POST") ||
      url.pathname === "/api/auth/session" ||
      (url.pathname === "/api/health" && method === "GET");
    if (!session && !publicEndpoint)
      return new Response('{"ok":false,"error":"unauthorized"}', {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    const headers = new Headers(init.headers || request?.headers);
    headers.delete("X-A-Pi-Activity");
    if (session && !publicEndpoint && activityPending) {
      headers.set("X-A-Pi-Activity", "1");
      activityPending = false;
    }
    if (!["GET", "HEAD", "OPTIONS"].includes(method))
      headers.set("X-A-Pi-Request", "1");
    const started = generation;
    const response = await rawFetch(input, {
      ...init,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!authentication && started !== generation)
      throw new Error("session_changed");
    if (response.status === 401 && session) {
      forget();
      options.onExpired("expired");
    }
    return response;
  }

  async function boot() {
    const response = await authenticatedFetch("/api/auth/session");
    if (response.status === 401) {
      forget();
      return null;
    }
    if (!response.ok) throw new Error("auth_network_error");
    const result = await response.json();
    if (
      result.authenticated !== true ||
      typeof result.username !== "string" ||
      typeof result.role !== "string" ||
      typeof result.expires_at !== "string"
    )
      throw new Error("auth_invalid_session");
    session = result;
    return session;
  }

  /** @param {string} username @param {string} password @param {boolean} remember @param {string} [otp] */
  async function login(username, password, remember, otp = "") {
    const response = await authenticatedFetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username,
        password,
        remember,
        ...(otp ? { otp } : {}),
      }),
    });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(result.error || "auth_network_error");
    }
    return boot();
  }

  async function logout() {
    try {
      return await authenticatedFetch("/api/auth/logout", { method: "POST" });
    } finally {
      forget();
    }
  }

  /** @param {string} username @param {string} password @param {string} invite_code */
  async function register(username, password, invite_code) {
    const response = await authenticatedFetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, invite_code }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "auth_network_error");
    if (typeof result.username !== "string")
      throw new Error("auth_invalid_session");
    return result.username;
  }

  return {
    fetch: authenticatedFetch,
    boot,
    login,
    register,
    logout,
    current: () => session,
    forget,
  };
}
