/* Staff access tokens stay in this module's closure. Local demo data is separate. */
const API_BASE = "https://urauw4ckw1.execute-api.us-east-1.amazonaws.com";
const TRANSACTION_KEY = "reshow.staff.login.v1";
const CALLBACKS = new Set([
  "https://dkbznu9f5nh9n.cloudfront.net/auth/callback.html",
  "http://localhost:8000/auth/callback.html",
]);
const RETURNS = new Set(["#overview", "#exhibits", "#labels", "#activity", "#reports", "#staff", "#live-demo"]);

export function base64url(bytes) {
  const encoded = btoa(String.fromCharCode(...bytes));
  const padding = encoded.indexOf("=");
  return (padding === -1 ? encoded : encoded.slice(0, padding)).replaceAll("+", "-").replaceAll("/", "_");
}

export function safeReturnHash(value) {
  return RETURNS.has(value) ? value : "#staff";
}

export class SessionError extends Error {
  constructor(message, status = 0, code = "") {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function createStaffSession(runtime = {}) {
  const fetcher = runtime.fetch || globalThis.fetch.bind(globalThis);
  const crypto = runtime.crypto || globalThis.crypto;
  const location = runtime.location || globalThis.location;
  const history = runtime.history || globalThis.history;
  const storage = runtime.storage || globalThis.sessionStorage;
  const now = runtime.now || Date.now;
  const setTimer = runtime.setTimeout || globalThis.setTimeout.bind(globalThis);
  const clearTimer = runtime.clearTimeout || globalThis.clearTimeout.bind(globalThis);
  const listeners = new Set();
  const channel = runtime.channel === undefined && typeof BroadcastChannel !== "undefined"
    ? new BroadcastChannel("reshow-staff-session-v1") : runtime.channel;
  let configuration = null, accessToken = null, refreshToken = null, expiresAt = 0, timer = null;
  let state = { status: "signed_out", user: null, message: "Sign in for live website account administration." };
  let generation = 0;

  function snapshot() {
    return { ...state, user: state.user ? structuredClone(state.user) : null };
  }
  function publish(update) {
    state = { ...state, ...update };
    for (const listener of listeners) listener(snapshot());
  }
  function clear(status, message) {
    generation += 1;
    try { storage.removeItem(TRANSACTION_KEY); } catch (_ignored) { /* Storage may be blocked. */ }
    accessToken = refreshToken = null;
    expiresAt = 0;
    if (timer !== null) clearTimer(timer);
    timer = null;
    publish({ status, user: null, message });
  }
  if (channel) channel.onmessage = (event) => {
    if (event.data?.type === "signout") clear("signed_out", "Staff sign-out in another tab cleared this session.");
  };

  async function fetchResponse(url, options = {}) {
    if (options.signal?.aborted) throw new SessionError("The live data request was cancelled.", 0, "request_cancelled");
    const controller = new AbortController();
    const signal = options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal;
    const timeout = setTimer(() => controller.abort(), 15000);
    try {
      return await fetcher(url, { ...options, credentials: "omit", cache: "no-store", signal });
    } catch (_error) {
      if (options.signal?.aborted && (!options.method || options.method === "GET")) {
        throw new SessionError("The live data request was cancelled.", 0, "request_cancelled");
      }
      throw new SessionError("The request did not finish. An action may already have happened; refresh current records before retrying.", 0, "network_error");
    } finally { clearTimer(timeout); }
  }
  async function jsonResponse(response) {
    let body;
    try { body = await response.json(); }
    catch (_error) { throw new SessionError("The service returned an unreadable response. Try refreshing the session.", response.status, "invalid_response"); }
    if (!response.ok) throw new SessionError(body.error?.message || "The operation could not be completed.", response.status, body.error?.code || "request_failed");
    return body;
  }
  async function config() {
    if (configuration) return configuration;
    const value = await jsonResponse(await fetchResponse(API_BASE + "/auth/config"));
    const callback = location.origin + "/auth/callback.html";
    if (!CALLBACKS.has(callback) || !value.allowed_callback_urls?.includes(callback)
        || !value.allowed_logout_urls?.includes(location.origin + "/")
        || !/^[A-Za-z0-9]{1,128}$/.test(value.client_id || "")
        || value.staff_api_prefix !== "/staff-api"
        || JSON.stringify(value.scopes) !== JSON.stringify(["reshow/staff"])) {
      throw new SessionError("Staff sign-in is not configured for this exact website address.", 0, "invalid_configuration");
    }
    let origin;
    for (const key of ["authorization_endpoint", "token_endpoint", "revocation_endpoint", "logout_endpoint"]) {
      const url = new URL(value[key]);
      if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || (origin && url.origin !== origin)) {
        throw new SessionError("The identity provider configuration could not be verified.", 0, "invalid_configuration");
      }
      origin = url.origin;
    }
    configuration = { ...value, redirect_uri: callback, logout_uri: location.origin + "/" };
    return configuration;
  }
  function random() {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    return base64url(bytes);
  }
  async function startSignIn(returnHash = "#staff") {
    if (["signing_in", "signing_out"].includes(state.status)) return;
    const loginGeneration = generation;
    publish({ status: "signing_in", message: "Opening secure staff sign-in…" });
    try {
      const settings = await config();
      const verifier = random(), oauthState = random();
      const challenge = base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
      if (loginGeneration !== generation) throw new SessionError("The staff session changed. Start sign-in again.", 401, "session_changed");
      const transaction = { state: oauthState, verifier, created: now(), returnHash: safeReturnHash(returnHash), redirectUri: settings.redirect_uri };
      storage.setItem(TRANSACTION_KEY, JSON.stringify(transaction));
      const url = new URL(settings.authorization_endpoint);
      url.search = new URLSearchParams({ response_type: "code", client_id: settings.client_id,
        redirect_uri: settings.redirect_uri, scope: "reshow/staff", state: oauthState,
        code_challenge_method: "S256", code_challenge: challenge }).toString();
      location.assign(url.href);
    } catch (error) {
      try { storage.removeItem(TRANSACTION_KEY); } catch (_ignored) { /* Browser storage may be blocked. */ }
      publish({ status: error.code === "network_error" ? "network_error" : "login_error", message: error.message });
      throw error;
    }
  }
  async function request(path, { method = "GET", body, version, signal } = {}) {
    if (!/^\/[A-Za-z0-9_/?=&%.,:-]*$/.test(path) || path.includes("..") || path.startsWith("//")) {
      throw new SessionError("Invalid application operation.", 400, "invalid_path");
    }
    if (!accessToken || now() >= expiresAt) {
      clear("session_expired", "Staff session expired. Sign in again to continue; local demo work is preserved.");
      throw new SessionError(state.message, 401, "session_expired");
    }
    const requestGeneration = generation;
    const headers = { authorization: "Bearer " + accessToken };
    if (body !== undefined) headers["content-type"] = "application/json";
    if (version !== undefined) headers["if-match"] = String(version);
    try {
      const result = await jsonResponse(await fetchResponse(API_BASE + "/staff-api" + path,
        { method, headers, signal, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) }));
      if (requestGeneration !== generation) throw new SessionError("The staff session changed. Refresh before continuing.", 401, "session_changed");
      return result;
    } catch (error) {
      if (requestGeneration !== generation) throw error;
      if (signal?.aborted && method === "GET") throw new SessionError("The live data request was cancelled.", 0, "request_cancelled");
      if (error.status === 401) clear("session_expired", "Staff session expired or was signed out. Sign in again to continue.");
      else if (error.status === 403 && error.code === "account_unavailable") clear("disabled_account", "Website account access is unavailable. Contact an Administrator.");
      throw error;
    }
  }
  async function readSession() {
    try {
      const response = await request("/session");
      if (!response.user?.staff_id || !["Administrator", "Operator", "Viewer"].includes(response.user.role)
          || response.user.account_status !== "active" || typeof response.user.permissions !== "object") {
        throw new SessionError("The application session could not be verified.", 401, "invalid_session");
      }
      publish({ status: "authenticated", user: response.user, message: "Live account permissions are enforced by the backend." });
      return snapshot();
    } catch (error) {
      if (error.code === "invalid_session") clear("login_error", error.message);
      else if (!error.status) publish({ status: "network_error", message: "Could not check the live session. Retry without restarting sign-in." });
      throw error;
    }
  }
  async function completeCallback() {
    if (location.pathname !== "/auth/callback.html") return false;
    const callbackGeneration = generation;
    const params = new URLSearchParams(location.search);
    let transaction;
    try { transaction = JSON.parse(storage.getItem(TRANSACTION_KEY) || "null"); }
    catch (_error) { transaction = null; }
    try { storage.removeItem(TRANSACTION_KEY); } catch (_ignored) { transaction = null; }
    // Remove authorization code and provider errors before any network call.
    history.replaceState(null, "", "/#staff");
    publish({ status: "signing_in", user: null, message: "Completing secure staff sign-in…" });
    try {
      if (!transaction || typeof transaction.created !== "number" || now() - transaction.created > 600000
          || now() < transaction.created || transaction.redirectUri !== location.origin + "/auth/callback.html"
          || !CALLBACKS.has(transaction.redirectUri)
          || !/^[A-Za-z0-9_-]{43,128}$/.test(transaction.verifier || "")
          || !/^[A-Za-z0-9_-]{43,128}$/.test(transaction.state || "")
          || !RETURNS.has(transaction.returnHash) || params.getAll("state").length !== 1
          || params.get("state") !== transaction.state || params.getAll("code").length > 1
          || params.getAll("error").length > 1 || params.has("code") === params.has("error")) {
        throw new SessionError("This sign-in attempt is invalid or expired. Start sign-in again.", 0, "invalid_callback");
      }
      if (params.has("error")) throw new SessionError("Staff sign-in was cancelled or could not be completed. You can try again.", 0, "login_cancelled");
      const code = params.get("code");
      if (!code || code.length > 4096) throw new SessionError("This sign-in attempt is invalid. Start sign-in again.", 0, "invalid_callback");
      const settings = await config();
      const response = await fetchResponse(settings.token_endpoint, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ grant_type: "authorization_code", client_id: settings.client_id, code,
          redirect_uri: settings.redirect_uri, code_verifier: transaction.verifier }).toString() });
      if (!response.ok) throw new SessionError("Staff sign-in could not be completed. Start a new sign-in attempt.", 0, "exchange_failed");
      const tokens = await response.json();
      if (callbackGeneration !== generation) throw new SessionError("The staff session changed. Start sign-in again.", 401, "session_changed");
      if (typeof tokens.access_token !== "string" || !tokens.access_token || tokens.access_token.length > 16384
          || tokens.token_type?.toLowerCase() !== "bearer" || !Number.isFinite(tokens.expires_in)
          || tokens.expires_in <= 0 || tokens.expires_in > 900
          || (tokens.refresh_token !== undefined && (typeof tokens.refresh_token !== "string" || tokens.refresh_token.length > 16384))) {
        throw new SessionError("The identity provider returned an unsupported session. Start sign-in again.", 0, "invalid_tokens");
      }
      generation += 1;
      accessToken = tokens.access_token;
      refreshToken = tokens.refresh_token || null;
      expiresAt = now() + tokens.expires_in * 1000;
      timer = setTimer(() => clear("session_expired", "Staff session expired. Sign in again to continue; local demo work is preserved."), tokens.expires_in * 1000);
      await readSession();
      history.replaceState(null, "", "/" + transaction.returnHash);
      return true;
    } catch (error) {
      if (!accessToken) clear(error.code === "network_error" ? "network_error" : "login_error", error.message);
      throw error;
    }
  }
  async function signOut() {
    const refresh = refreshToken;
    const settings = configuration;
    if (!accessToken || !settings) {
      clear("signed_out", "Staff session cleared from this browser tab.");
      channel?.postMessage({ type: "signout" });
      return { logoutUrl: null, applicationRevoked: false };
    }
    publish({ status: "signing_out", user: null, message: "Revoking website sessions…" });
    channel?.postMessage({ type: "signout" });
    let applicationRevoked = false;
    try { applicationRevoked = (await request("/session/logout", { method: "POST", body: {} })).application_sessions_revoked === true; }
    catch (_error) { /* Clear local credentials even when revocation is unavailable. */ }
    if (refresh) {
      try { await fetchResponse(settings.revocation_endpoint, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ token: refresh, client_id: settings.client_id }).toString() }); }
      catch (_error) { /* Provider logout is still offered below. */ }
    }
    clear("signed_out", applicationRevoked ? "Website sessions revoked. Ending the provider browser session…" :
      "This tab is signed out. Global revocation could not be confirmed; another session may remain valid for up to 15 minutes. End the provider session, then contact an Administrator if needed.");
    const url = new URL(settings.logout_endpoint);
    url.search = new URLSearchParams({ client_id: settings.client_id, logout_uri: settings.logout_uri }).toString();
    return { logoutUrl: url.href, applicationRevoked };
  }
  return { snapshot, config, startSignIn, completeCallback, readSession, signOut, request,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    dispose() { clear("signed_out", "Staff session cleared."); listeners.clear(); channel?.close(); } };
}

export const staffSession = typeof window !== "undefined" ? createStaffSession() : null;
