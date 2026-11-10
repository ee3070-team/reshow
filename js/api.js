import { api as demo } from "./services.js";
import { createSessionAuth } from "./session-auth.js";
import { staffSession, createStaffSession } from "./auth-session.js";
import { createLiveApi } from "./live-api.js";

// Production and tests use the same session/facade wiring.
export function createApi(session, runtime = {}) {
  const location = runtime.location || globalThis.location;
  const document = runtime.document || globalThis.document;
  const auth = createSessionAuth(session, location);
  document?.documentElement?.setAttribute("data-api-build", "2026-11-04-live-timing");
  // Read-only local timing evidence. Never retain request URLs/IDs, headers,
  // response bodies, signed media links or authentication values.
  async function timedLiveRequest(path, options) {
    const target = location?.hostname === "localhost" ? document?.querySelector("#page-content") : null;
    const loadId = target?.dataset.loadState === "loading" ? target.dataset.loadId : null;
    const started = performance.now();
    let outcome = "ok";
    try { return await auth.request(path, options); }
    catch (error) { outcome = error.status || "network_error"; throw error; }
    finally {
      if (loadId && target.dataset.loadId === loadId && target.dataset.loadState === "loading") {
        try {
          const entries = JSON.parse(target.dataset.requestTimings || "[]");
          entries.push({ resource: path.split("?")[0].split("/")[2], milliseconds: Math.round(performance.now() - started), outcome });
          target.dataset.requestTimings = JSON.stringify(entries.slice(-60));
        } catch { /* Optional diagnostics must never change request outcomes. */ }
      }
    }
  }
  const live = createLiveApi({ request: timedLiveRequest, getSession: auth.getSession, can: auth.can, onChange: auth.subscribe });
  let mode = "demo";
  const listeners = new Set();
  const emit = (event) => Promise.allSettled([...listeners].map(async listener => listener(event)));
  demo.subscribe(() => { if (mode === "demo") void emit(); });
  const liveChanges = (event) => { if (mode === "live") return emit(event); };
  live.subscribe(liveChanges);
  const controls = {
    getMode: () => mode,
    isLive: () => mode === "live",
    async initialize() {
      // Demo keeps its explicitly labelled local session; live tokens never survive reload.
      if (location?.pathname === "/auth/callback.html") mode = "live";
      return auth.initialize();
    },
    async selectMode(next) {
      if (!["demo", "live"].includes(next)) throw new Error("Choose Demo or Live mode.");
      if (mode === next) return;
      if (mode === "live" && auth.getSession()) throw new Error("Sign out of the live workspace before switching modes.");
      await demo.signOut();
      mode = next;
      await emit();
    },
    async startLiveLogin() { mode = "live"; return auth.startLogin(); },
    getSession: () => mode === "live" ? auth.getSession() : demo.getSession(),
    refreshSession: () => mode === "live" ? auth.refreshSession() : demo.getSession(),
    can: (permission) => mode === "live" ? live.can(permission) : demo.can(permission),
    getDemoMode: () => mode === "live" ? "normal" : demo.getDemoMode(),
    async signIn(email) {
      if (mode !== "demo") throw new Error("Use the secure provider button for live sign in.");
      return demo.signIn(email);
    },
    signOut: () => mode === "live" ? auth.signOut() : demo.signOut(),
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
  // The facade also exposes adapter-only operations without coupling pages to its implementation.
  return new Proxy(controls, {
    get(target, key) {
      if (key in target) return target[key];
      const adapter = mode === "live" ? live : demo;
      const value = adapter[key];
      if (typeof value === "function") return value.bind(adapter);
      return value;
    },
  });
}

export const api = createApi(staffSession || createStaffSession({ channel: null }));
