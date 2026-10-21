import { createAuthClient } from "./auth-client.mjs";
/** @type {ReturnType<typeof createAuthClient> | null} */
let client = null;
/** @type {(reason?:string)=>void} */
let expired = () => {};
/** @param {Parameters<typeof createAuthClient>[0]} options */
export function configureApiClient(options) {
  client = createAuthClient(options);
  return client;
}
/** @param {(reason?:string)=>void} handler */
export function setSessionExpiredHandler(handler) {
  expired = handler;
}
/** Shared session; independent of app.js evaluation order. */
export function getAuthClient() {
  if (!client) {
    if (typeof window === "undefined")
      throw new Error("api_client_not_configured");
    client = createAuthClient({
      fetch: window.fetch.bind(window),
      origin: window.location.href,
      onExpired: (reason) => expired(reason),
    });
  }
  return client;
}
/** @type {typeof fetch} */
export function apiFetch(input, init) {
  return getAuthClient().fetch(input, init);
}
