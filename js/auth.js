// OAuth access tokens and the authoritative staff session stay in this module's memory.
// Only the short-lived PKCE transaction survives the provider redirect in sessionStorage.
const TRANSACTION_KEY = "reshow-pkce-v1";
const TRANSACTION_MS = 10 * 60 * 1000;
const RETURN_TABS = new Set(["overview", "exhibits", "labels", "activity", "reports", "staff"]);
export const DEFAULT_API_ORIGIN = "https://urauw4ckw1.execute-api.us-east-1.amazonaws.com";

export function createAuth(options = {}) {
  const fetcher = options.fetch || globalThis.fetch.bind(globalThis);
  const crypto = options.crypto || globalThis.crypto;
  const location = options.location || globalThis.location;
  const history = options.history || globalThis.history;
  let storage = options.storage;
  if (!storage) { try { storage = globalThis.sessionStorage; } catch { /* Live login reports blocked storage when started. */ } }
  const now = options.now || Date.now;
  const schedule = options.setTimeout || globalThis.setTimeout;
  const unschedule = options.clearTimeout || globalThis.clearTimeout;
  const requestTimeoutMs = options.requestTimeoutMs || 15000;
  const origin = new URL(options.apiOrigin || DEFAULT_API_ORIGIN).origin;
  const listeners = new Set();
  let config, token = null, expiresAt = 0, user = null, expiryTimer, generation = 0;
  const emit = () => listeners.forEach(listener => listener());
  const issue = (message, extra = {}) => Object.assign(new Error(message), extra);
  function clear() {
    ++generation;
    token = user = null;
    expiresAt = 0;
    unschedule(expiryTimer);
    emit();
  }
  function getSession() {
    if (token && now() >= expiresAt) clear();
    return token && user ? structuredClone(user) : null;
  }
  const aliases = { reports: "view", operate: "edit_exhibits", staff: "manage_staff" };
  function can(permission) {
    return getSession()?.permissions?.[aliases[permission] || permission] === true;
  }
  async function readResponse(response) {
    const raw = await response.text();
    let data;
    try { data = raw ? JSON.parse(raw) : null; }
    catch { throw issue("The live service returned an unreadable response.", { status: response.status }); }
    if (!response.ok) throw issue(data?.error?.message || data?.message || `Live request failed (${response.status}).`, {
      status: response.status, code: data?.error?.code || "http_error", details: data,
    });
    return data;
  }
  async function fetchResult(url, options = {}) {
    const controller = new AbortController();
    const timer = globalThis.setTimeout(() => controller.abort(), requestTimeoutMs);
    const abort = () => controller.abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    if (options.signal?.aborted) controller.abort();
    try {
      const response = await fetcher(url, { ...options, signal: controller.signal });
      return await readResponse(response);
    } catch (error) {
      if (controller.signal.aborted) throw issue("The live request timed out. Check the current records before retrying a change.", { code: "request_timeout" });
      throw error;
    } finally {
      globalThis.clearTimeout(timer);
      options.signal?.removeEventListener("abort", abort);
    }
  }
  async function loadConfig() {
    if (config) return config;
    const candidate = await fetchResult(`${origin}/auth/config`, { credentials: "omit", cache: "no-store" });
    for (const field of ["authorization_endpoint", "token_endpoint", "logout_endpoint"]) {
      if (!candidate?.[field] || new URL(candidate[field]).protocol !== "https:")
        throw issue("The live login configuration has an invalid provider endpoint.");
    }
    if (!candidate.client_id || !Array.isArray(candidate.scopes) || candidate.staff_api_prefix !== "/staff-api")
      throw issue("The live login configuration is incomplete.");
    config = candidate;
    return config;
  }
  function allowedUri(values, fallback, path) {
    const desired = new URL(path, location.origin).href;
    const allowed = Array.isArray(values) ? values : [fallback];
    if (!allowed.includes(desired)) throw issue(`Live login is not configured for ${location.origin}. Use an allowed application address.`);
    return desired;
  }
  function base64url(bytes) {
    const encoded = btoa(String.fromCharCode(...bytes));
    const padding = encoded.indexOf("=");
    return (padding === -1 ? encoded : encoded.slice(0, padding)).replaceAll("+", "-").replaceAll("/", "_");
  }
  async function startLogin() {
    const cfg = await loadConfig();
    const redirect = allowedUri(cfg.allowed_callback_urls, cfg.redirect_uri, "/auth/callback.html");
    const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
    const state = base64url(crypto.getRandomValues(new Uint8Array(32)));
    const challenge = base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
    try {
      const tab = location.hash?.slice(1);
      storage.setItem(TRANSACTION_KEY, JSON.stringify({ state, verifier, redirect, createdAt: now(), returnTab: RETURN_TABS.has(tab) ? tab : "overview" }));
    } catch { throw issue("Allow session storage in this tab to complete secure live sign in."); }
    const target = new URL(cfg.authorization_endpoint);
    target.search = new URLSearchParams({ response_type: "code", client_id: cfg.client_id,
      redirect_uri: redirect, scope: cfg.scopes.join(" "), state,
      code_challenge_method: "S256", code_challenge: challenge }).toString();
    location.assign(target.href);
  }
  async function request(path, options = {}) {
    if (!token || now() >= expiresAt) {
      clear();
      throw issue("Your live session ended. Sign in again to continue.", { status: 401, code: "session_expired" });
    }
    const url = new URL(path.startsWith("/staff-api/") ? path : `/staff-api/${path.replace(/^\//, "")}`, origin);
    if (url.origin !== origin || !url.pathname.startsWith("/staff-api/")) throw issue("Invalid staff API request.");
    const method = (options.method || "GET").toUpperCase();
    const requestGeneration = generation;
    const headers = new Headers(options.headers);
    headers.set("Authorization", `Bearer ${token}`);
    let body = options.body;
    if (body != null && typeof body === "object") {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(body);
    }
    try {
      const result = await fetchResult(url.href, { ...options, method, headers, body, credentials: "omit", cache: "no-store", redirect: "error" });
      if (requestGeneration !== generation || !token)
        throw issue("The live session changed while this request was running. Sign in again.", { status: 401, code: "session_changed" });
      return result;
    }
    catch (error) {
      if (requestGeneration === generation && (error.status === 401 || (url.pathname === "/staff-api/session" && error.status === 403))) clear();
      if (error.status) {
        if (method !== "GET" && (error.status < 400 || error.status >= 500)) error.ambiguous = true;
        throw error;
      }
      throw issue(error.code === "request_timeout" ? error.message : method === "GET" ? "Cannot reach the live service. Check your connection and retry." :
        "The connection ended before confirmation. Refresh to check whether the request was saved before retrying.",
        { code: error.code || "network_error", ambiguous: method !== "GET" });
    }
  }
  async function refreshSession() {
    if (!token) return null;
    const session = await request("/staff-api/session");
    if (!session?.user?.staff_id || session.user.account_status !== "active" || !session.user.permissions) {
      clear();
      throw issue("The live service did not return an active staff identity.");
    }
    const next = { ...session.user, id: session.user.staff_id };
    const changed = JSON.stringify(next) !== JSON.stringify(user);
    user = next;
    if (changed) emit();
    return getSession();
  }
  async function initialize() {
    if (location.pathname !== "/auth/callback.html") return false;
    const query = new URLSearchParams(location.search);
    let transaction;
    try { transaction = JSON.parse(storage.getItem(TRANSACTION_KEY) || "null"); }
    catch { /* Invalid transactions cannot authenticate. */ }
    // Remove OAuth values immediately, before any network request or app rendering.
    history.replaceState(null, "", `/#${RETURN_TABS.has(transaction?.returnTab) ? transaction.returnTab : "overview"}`);
    try { storage.removeItem(TRANSACTION_KEY); } catch { /* A blocked store cannot produce a valid transaction. */ }
    if (!transaction || !query.get("state") || query.get("state") !== transaction.state ||
        !Number.isFinite(transaction.createdAt) ||
        now() - transaction.createdAt > TRANSACTION_MS || now() < transaction.createdAt ||
        typeof transaction.verifier !== "string" || !/^[A-Za-z0-9_-]{43,128}$/.test(transaction.verifier))
      throw issue("This sign-in request expired or could not be verified. Start live sign in again.");
    if (query.has("error")) throw issue("Live sign in was cancelled or declined. You can try again.");
    if (!query.get("code")) throw issue("The login provider did not return an authorization code.");
    const cfg = await loadConfig();
    if (transaction.redirect !== allowedUri(cfg.allowed_callback_urls, cfg.redirect_uri, "/auth/callback.html"))
      throw issue("The login callback address changed. Start sign in again.");
    const result = await fetchResult(cfg.token_endpoint, {
      method: "POST", credentials: "omit", cache: "no-store",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "authorization_code", client_id: cfg.client_id,
        code: query.get("code"), redirect_uri: transaction.redirect, code_verifier: transaction.verifier }).toString(),
    });
    if (!result?.access_token || result.token_type?.toLowerCase() !== "bearer" || !(Number(result.expires_in) > 0))
      throw issue("The login provider returned an invalid access token response.");
    ++generation;
    token = result.access_token;
    expiresAt = now() + Math.min(Number(result.expires_in), Number(cfg.access_token_seconds) || 900) * 1000;
    expiryTimer = schedule(clear, expiresAt - now());
    try { await refreshSession(); } catch (error) { clear(); throw error; }
    return true;
  }
  async function signOut() {
    const cfg = await loadConfig();
    const logoutUri = allowedUri(cfg.allowed_logout_urls, cfg.logout_uri, "/");
    if (token && now() < expiresAt) {
      try { await request("/staff-api/session/logout", { method: "POST", body: {} }); }
      catch (error) { if (error.status !== 401) throw error; }
    }
    clear();
    const target = new URL(cfg.logout_endpoint);
    target.search = new URLSearchParams({ client_id: cfg.client_id, logout_uri: logoutUri }).toString();
    location.assign(target.href);
  }
  return { initialize, startLogin, getSession, refreshSession, request, can, signOut, clear,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); } };
}
