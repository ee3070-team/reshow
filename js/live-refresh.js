// One load at a time. The delay starts only after the previous load has finished.
export const LIVE_REFRESH_MS = 30000;

export function createLiveRefresh({ load, cancel, changed = () => {}, onError = () => {},
  setTimer = globalThis.setTimeout.bind(globalThis), clearTimer = globalThis.clearTimeout.bind(globalThis),
  interval = LIVE_REFRESH_MS } = {}) {
  let active = false, enabled = true, busy = false, disposed = false, timer = null, wake = false;
  const publish = () => { if (!disposed) changed({ active, enabled, busy }); };
  function clear() { if (timer !== null) clearTimer(timer); timer = null; }
  function schedule() {
    clear();
    if (active && enabled && !busy && !disposed) timer = setTimer(() => { timer = null; run(); }, interval);
  }
  async function run() {
    if (!active || busy || disposed) return;
    clear();
    busy = true;
    publish();
    try { await load(); }
    catch (error) { if (!disposed) onError(error); }
    finally {
      busy = false;
      publish();
      if (wake && active && !disposed) { wake = false; run(); }
      else schedule();
    }
  }
  return {
    run,
    setActive(next, loadNow = enabled) {
      if (disposed || active === next) return;
      active = next;
      clear();
      if (!active) { wake = false; cancel(); }
      else if (loadNow) { if (busy) wake = true; else run(); }
      publish();
      schedule();
    },
    setEnabled(next) { enabled = next; publish(); schedule(); },
    dispose() { disposed = true; active = false; wake = false; clear(); cancel(); },
  };
}
