// Adapt the shared staff session to the six-tab dashboard's data boundary.
// Live showcase and account administration use that same session instance.
export function createSessionAuth(session, location = globalThis.location) {
  const getSession = () => {
    const user = session.snapshot().user;
    return user ? { ...user, id: user.staff_id } : null;
  };
  return {
    initialize: () => session.completeCallback(),
    startLogin: () => session.startSignIn(location?.hash || "#overview"),
    getSession,
    async refreshSession() { await session.readSession(); return getSession(); },
    can: permission => getSession()?.permissions?.[permission] === true,
    subscribe(listener) {
      // Re-reading an unchanged identity must not invalidate in-flight reads.
      let previous = JSON.stringify(getSession());
      return session.subscribe(() => {
        const next = JSON.stringify(getSession());
        if (next !== previous) { previous = next; listener(); }
      });
    },
    async request(path, options = {}) {
      const { headers: suppliedHeaders, version: suppliedVersion, ...requestOptions } = options;
      const headerVersion = new Headers(suppliedHeaders).get("If-Match");
      if (headerVersion !== null && suppliedVersion !== undefined && headerVersion !== String(suppliedVersion)) {
        throw Object.assign(new Error("Conflicting record versions. Reload the record before editing."), { code: "version_mismatch" });
      }
      // The shared session is the sole owner of HTTP headers and credentials.
      const version = headerVersion ?? suppliedVersion;
      const relative = path.startsWith("/staff-api/") ? path.slice("/staff-api".length) : path;
      return session.request(relative, { ...requestOptions, ...(version !== undefined ? { version } : {}) });
    },
    async signOut() {
      const result = await session.signOut();
      if (!result.applicationRevoked && result.logoutUrl) {
        throw new Error(session.snapshot().message);
      }
      if (result.logoutUrl) location.assign(result.logoutUrl);
    },
  };
}
