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
      const headers = new Headers(options.headers);
      const version = headers.get("If-Match");
      const relative = path.startsWith("/staff-api/") ? path.slice("/staff-api".length) : path;
      return session.request(relative, { ...options, ...(version !== null ? { version } : {}) });
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
