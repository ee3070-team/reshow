// Shared API/session and login modules were merged in PR #9 (b7dabd4).
import { api } from "./api.js";
import { renderLogin } from "./login.js";
import { renderLiveAccounts } from "./live-accounts.js";
import {
  icon,
  esc,
  formatDate,
  formatTime,
  showDialog,
  confirmAction,
  toast,
  reportError,
  handleAction,
} from "./ui.js";
import { render as overview } from "./pages/overview.js";
import { render as exhibits } from "./pages/exhibits.js";
import { render as labels } from "./pages/labels.js";
import { render as activity } from "./pages/activity.js";
import { render as reports } from "./pages/reports.js";
import { render as staff } from "./pages/staff.js";
import { render as liveDemo } from "./pages/live-demo.js";
import { render as technologyShowcase } from "./pages/technology-showcase.js";
const pages = {
  overview: {
    title: "Showcase overview",
    subtitle: "Case 01 / School exhibition",
    icon: "home",
    render: overview,
  },
  exhibits: {
    title: "Exhibits",
    subtitle: "Manage exhibit profiles and showcase assignments",
    icon: "cube",
    render: exhibits,
  },
  labels: {
    title: "Electronic labels",
    subtitle: "Edit exhibit information and confirm delivery to the showcase",
    icon: "tag",
    render: labels,
  },
  activity: {
    title: "Activity",
    subtitle: "Anonymous proximity activity and operational events",
    icon: "activity",
    render: activity,
  },
  reports: {
    title: "Reports",
    subtitle: "Review exhibition activity and display conditions",
    icon: "report",
    render: reports,
  },
  staff: {
    title: "Staff access",
    subtitle: "Manage staff permissions and showcase access cards",
    icon: "users",
    render: staff,
  },
  "live-demo": {
    title: "Live showcase",
    subtitle: "AWS test data and Python updates",
    icon: "display",
    live: true,
    render: liveDemo,
  },
  technology: {
    title: "3D & AI studio",
    subtitle: "Preview 3D models and generate exhibit details from an image",
    icon: "cube",
    render: technologyShowcase,
  },
};
const canNavigate = tab => !api.isLive() || pages[tab]?.liveReady !== false;
let current = "overview",
  params = {},
  cleanup = null,
  dirty = false,
  renderId = 0;
const content = document.querySelector("#page-content");
const loginScreen = document.querySelector("#login-screen");
let sessionUser = null, loginCleanup = null, accountCleanup = null;

function updateIdentity(user) {
  sessionUser = user;
  const initials = user.name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase();
  document.querySelector("#session-identity").innerHTML = `<span class="avatar">${esc(initials)}</span><span>${esc(user.name)}<small>${esc(user.role)} · ${api.isLive() ? "Live" : "Demo"} session</small></span>`;
  document.querySelectorAll("[data-page]").forEach(link => { link.hidden = !canNavigate(link.dataset.page); });
  document.querySelector("#demo-controls").hidden = !api.can("demo");
  document.querySelector("#workspace-mode").textContent = api.isLive() ? "Live backend" : "Demo data";
  document.querySelector("#workspace-note").textContent = api.isLive() ? "Connected staff workspace" : "Prototype · Case 01";
  document.querySelector("#live-refresh").hidden = !api.isLive();
  document.querySelector("#staff-sign-in").hidden = !api.isLive();
  document.querySelector("#mode-footer").textContent = api.isLive() ? "Live backend · Device execution requires producer acknowledgement · All times HKT (UTC+8)" : "Simulated devices & acknowledgements · All times HKT (UTC+8)";
}

function publicEditorText() {
  return api.isLive() && dirty && ["exhibits", "labels", "overview"].includes(current)
    ? [...document.querySelectorAll('#page-content input:not([type="password"]):not([type="email"]):not([type="hidden"]), #page-content textarea, #page-content select, dialog input:not([type="password"]):not([type="email"]):not([type="hidden"]), dialog textarea')]
      .filter(field => !["button", "submit", "file", "checkbox", "radio"].includes(field.type) && field.value)
      .map(field => `${field.name || field.id || "Editor field"}: ${field.value}`).join("\n\n") : "";
}
function showLogin(message = "") {
  // Keep non-sensitive editor text in memory so expiry does not silently destroy it.
  const preserved = publicEditorText();
  ++renderId;
  cleanup?.();
  cleanup = null;
  loginCleanup?.();
  accountCleanup?.();
  accountCleanup = null;
  sessionUser = null;
  dirty = false;
  content.replaceChildren();
  document.querySelectorAll("dialog").forEach(dialog => dialog.remove());
  document.querySelector("#toast-region").replaceChildren();
  document.querySelector(".sidebar").hidden = true;
  document.querySelector("#main").hidden = true;
  document.querySelector("#session-identity").replaceChildren();
  document.body.classList.add("signed-out");
  loginScreen.hidden = false;
  document.title = "ReShow · Staff sign in";
  loginCleanup = renderLogin(loginScreen, api, async () => {
    await navigate(location.hash.slice(1) || "overview", {}, true);
  }, message);
  if (preserved) {
    const note = document.createElement("div");
    note.className = "notice info preserved-editor";
    const label = document.createElement("label");
    label.className = "field";
    label.textContent = "Unsaved editor text — copy before continuing to live sign in. This copy remains only in this page's memory.";
    const text = document.createElement("textarea");
    text.readOnly = true;
    text.rows = 6;
    text.value = preserved;
    label.append(text);
    note.append(label);
    loginScreen.querySelector(".login-card").append(note);
  }
}

async function requireSession() {
  const user = await api.getSession();
  if (!user) {
    if (sessionUser || loginScreen.hidden) showLogin(`Your ${api.isLive() ? "live" : "demo"} session ended. Please sign in again.`);
    return false;
  }
  updateIdentity(user);
  return true;
}
document.querySelector("#navigation").innerHTML = Object.entries(pages)
  .map(
    ([key, p]) =>
      `<a class="nav-link" data-page="${key}" href="#${key}">${icon(p.icon)}<span>${key === "labels" ? "Labels" : key === "overview" ? "Overview" : p.title}</span></a>`,
  )
  .join("");
document.querySelector("#report-shortcut").innerHTML =
  icon("report") + "Generate report";
document.querySelector("#report-shortcut").onclick = () => navigate("reports").catch(reportError);
document.querySelectorAll("[data-page]").forEach((a) =>
  a.addEventListener("click", (e) => {
    e.preventDefault();
    navigate(a.dataset.page).catch(reportError);
  }),
);
document.querySelector(".brand").onclick = (e) => {
  e.preventDefault();
  navigate("overview").catch(reportError);
};
async function canLeave() {
  return (
    !dirty ||
    (await confirmAction(
      "Discard unsaved changes?",
      "Your unsaved edits will be lost. Saved drafts and acknowledged device state will be kept.",
      "Discard changes",
    ))
  );
}
async function navigate(tab, nextParams = {}, replace = false) {
  if (!(await requireSession())) return;
  if (!pages[tab] || !canNavigate(tab)) tab = "overview";
  if (!(await canLeave())) return;
  dirty = false;
  current = tab;
  params = nextParams;
  history[replace ? "replaceState" : "pushState"](
    { tab, params },
    "",
    `#${tab}`,
  );
  window.scrollTo({ top: 0, behavior: "instant" });
  await refresh();
}
async function refresh() {
  if (!(await requireSession())) return;
  const id = ++renderId;
  const loadStarted = performance.now();
  content.dataset.frontendBuild = "2026-11-04-scoped-snapshots";
  content.dataset.loadState = "loading";
  content.dataset.loadId = String(id);
  content.dataset.requestTimings = "[]";
  delete content.dataset.loadMs;
  cleanup?.();
  cleanup = null;
  loginCleanup?.();
  loginCleanup = null;
  loginScreen.replaceChildren();
  loginScreen.hidden = true;
  document.querySelector(".sidebar").hidden = false;
  document.querySelector("#main").hidden = false;
  document.body.classList.remove("signed-out");
  const page = pages[current];
  document.title = `ReShow · ${page.title}`;
  document.querySelector("#page-title").textContent = page.title;
  document.querySelector("#page-subtitle").textContent = page.subtitle;
  document.querySelector("#workspace-mode").textContent = page.live ? "Live AWS showcase" : api.isLive() ? "Live backend" : "Demo data";
  document.querySelector("#demo-controls").hidden = Boolean(page.live) || !api.can("demo");
  document.querySelector("#report-shortcut").hidden = ![
    "overview",
    "activity",
  ].includes(current) || !canNavigate("reports");
  document.querySelectorAll("[data-page]").forEach((a) => {
    a.classList.toggle("active", a.dataset.page === current);
    if (a.dataset.page === current) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  content.innerHTML =
    `<div class="loading-state" role="status"><div class="spinner"></div><span>Loading ${api.isLive() ? "live" : "demo"} records…</span></div>`;
  try {
    // The Live header needs only a clock and mode. Let the page fetch its own
    // records instead of loading the entire workspace before every navigation.
    const snap = api.isLive() ? { now: new Date().toISOString() } : await api.getSnapshot();
    if (id !== renderId) return;
    document.querySelector("#demo-date").textContent =
      `${formatDate(snap.now)} · ${api.isLive() ? "Live refresh" : "Demo clock"}`;
    updateBanner(snap);
    const stage = document.createElement("div");
    const loading = content.firstElementChild;
    content.append(stage);
    if (current === "staff" && !api.can("staff")) {
      stage.innerHTML = '<div class="card empty"><h3>Staff access permission required</h3><p>Your current account cannot manage staff. Contact an administrator for access.</p></div>';
      content.dataset.loadMs = String(Math.round(performance.now() - loadStarted));
      content.dataset.loadState = "ready";
      loading.remove();
      return;
    }
    if (!canNavigate(current)) {
      stage.innerHTML = '<div class="card empty"><h3>Live page not yet available</h3><p><a href="#overview">Return to Overview</a> to use the available Live features.</p></div>';
      content.dataset.loadState = "ready";
      loading.remove();
      return;
    }
    const off = await page.render(stage, {
      api,
      navigate,
      canNavigate,
      params,
      refresh,
      setDirty: (value) => {
        if (id === renderId) dirty = value;
      },
    });
    if (id === renderId) {
      cleanup = off;
      loading.remove();
      content.dataset.loadMs = String(Math.round(performance.now() - loadStarted));
      content.dataset.loadState = "ready";
    }
    else off?.();
  } catch (error) {
    if (id !== renderId) return;
    content.dataset.loadState = "error";
    if (!(await requireSession())) return;
    content.innerHTML = `<div class="card empty"><h3>Could not load ${api.isLive() ? "live" : "demo"} records</h3><p>${esc(error.message)}</p><button id="retry-page" class="button" style="margin-top:16px">Try again</button></div>`;
    document.querySelector("#retry-page").onclick = refresh;
  }
}
function updateBanner(snap) {
  const mode = api.getDemoMode();
  const banner = document.querySelector("#scenario-banner");
  const messages = {
    loading: "Loading demo: requests are deliberately delayed.",
    empty:
      "Empty demo: no records or readings are available. Stored demo edits are preserved.",
    offline:
      "Offline demo: displayed readings are stale. Device changes cannot be acknowledged.",
    pending:
      "Pending demo: commands wait for you to acknowledge them in Demo controls.",
    failed:
      "Failed delivery demo: device commands will fail after a simulated wait.",
  };
  const message = api.isLive() ? (snap.liveWarning || "Live backend. Automatic refresh checks again 5 seconds after each refresh finishes. Requested device changes remain pending until producer acknowledgement.") : messages[mode] || snap.storageWarning;
  banner.hidden = !message;
  banner.innerHTML = message
    ? `<div class="notice ${mode === "offline" || mode === "failed" ? "warning" : "info"}">${icon("info")}${esc(message)}</div>`
    : "";
}
document.querySelector("#demo-controls").onclick = handleAction(async () => {
  if (!(await requireSession()) || !api.can("demo")) return;
  const snap = await api.getSnapshot();
  const mode = api.getDemoMode();
  const choices = [
    ["normal", "Normal", "Readings and acknowledgements"],
    ["loading", "Loading", "Slow service responses"],
    ["empty", "Empty", "No records or readings"],
    ["offline", "Offline", "Stale readings; delivery fails"],
    ["pending", "Pending", "Hold device acknowledgements"],
    ["failed", "Failed", "Simulated command failure"],
  ];
  showDialog(
    "Development / demo controls",
    `<p>All readings, scans, permissions and device responses are simulated.</p><div class="demo-mode-grid">${choices.map(([value, title, description]) => `<label class="demo-mode-option"><input type="radio" name="mode" value="${value}" ${value === mode ? "checked" : ""}>${title}<small>${description}</small></label>`).join("")}</div><p class="demo-clock">Demo clock: ${esc(formatDate(snap.now))}, ${esc(formatTime(snap.now))}. Sample exhibition hours: 09:00–17:00 HKT. The clock is fixed and advances for demo operations.</p><div class="actions"><button class="button" data-settle>Acknowledge pending</button><button class="button danger" data-reset>Reset demo records</button><button class="button primary" data-apply>Apply scenario</button></div>`,
    (d) => {
      d.querySelector("[data-apply]").onclick = handleAction(async () => {
        if (!(await canLeave())) return;
        dirty = false;
        await api.setDemoMode(d.querySelector("[name=mode]:checked").value);
        d.close();
        await refresh();
      });
      d.querySelector("[data-settle]").onclick = handleAction(async () => {
        await api.settlePending();
        toast("Pending demo commands processed.");
        d.close();
        if (!dirty) await refresh();
      });
      d.querySelector("[data-reset]").onclick = handleAction(async () => {
        if (
          await confirmAction(
            "Reset demo records?",
            "This removes saved demo edits, reports and operation history from this browser and restores the sample exhibition.",
            "Reset demo",
          )
        ) {
          dirty = false;
          await api.resetDemo();
          d.close();
          await refresh();
        }
      });
    },
  );
});
window.addEventListener("beforeunload", (e) => {
  if (dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
document.querySelector("#staff-sign-in").onclick = handleAction(async () => {
  if (!(await canLeave()) || !api.isLive()) return;
  showDialog("Live website account", '<div data-account-panel></div>', dialog => {
    let stop, closed = false;
    accountCleanup = () => { closed = true; stop?.(); };
    dialog.addEventListener("close", () => { accountCleanup?.(); accountCleanup = null; }, { once: true });
    renderLiveAccounts(dialog.querySelector("[data-account-panel]")).then(dispose => {
      if (closed) dispose(); else stop = dispose;
    }).catch(reportError);
  });
});

document.querySelector("#sign-out").onclick = async () => {
  if (!(await canLeave())) return;
  try { await api.signOut(); }
  catch (error) { toast(`Sign out could not be confirmed: ${error.message}`, "error"); return; }
  dirty = false;
  history.replaceState({ tab: "overview", params: {} }, "", "#overview");
  showLogin();
};
// Keep the demo clock and storage warnings current without replacing page editors.
api.subscribe(async (event) => {
  if (!sessionUser) return;
  const previousPermissions = JSON.stringify([sessionUser.role, sessionUser.permissions]);
  if (!(await requireSession())) return;
  if (event?.error) {
    const banner = document.querySelector("#scenario-banner");
    banner.hidden = false;
    banner.innerHTML = `<div class="notice warning">Automatic refresh failed: ${esc(event.error.message)} Displayed records may be out of date. Retrying automatically.</div>`;
    return;
  }
  if (previousPermissions !== JSON.stringify([sessionUser.role, sessionUser.permissions])) {
    const preserved = publicEditorText();
    dirty = false;
    await refresh();
    if (preserved && sessionUser) {
      const note = document.createElement("section");
      note.className = "notice warning";
      const label = document.createElement("label");
      label.className = "field";
      label.textContent = "Permissions changed. Your unsaved editor text is retained here for copying before leaving this page.";
      const field = document.createElement("textarea");
      field.readOnly = true;
      field.rows = 5;
      field.value = preserved;
      label.append(field); note.append(label); content.prepend(note);
    }
    return;
  }
  try {
    const snapshot = api.isLive() ? { now: new Date().toISOString() } : await api.getSnapshot();
    if (!sessionUser) return;
    updateBanner(snapshot);
    document.querySelector("#demo-date").textContent =
      `${formatDate(snapshot.now)} · ${api.isLive() ? "Live refresh" : "Demo clock"}`;
  } catch {
    await requireSession();
  }
});
window.addEventListener("popstate", handleAction(async (e) => {
  if (!(await requireSession())) return;
  const target = e.state?.tab || location.hash.slice(1);
  if (!(await canLeave())) {
    history.pushState({ tab: current, params }, "", `#${current}`);
    return;
  }
  dirty = false;
  current = pages[target] ? target : "overview";
  params = e.state?.params || {};
  await refresh();
}));
// Keep external hash links useful without bypassing the unsaved-edits guard.
window.addEventListener("hashchange", () => {
  const target = location.hash.slice(1);
  if (pages[target] && target !== current) navigate(target, {}, true).catch(reportError);
});
// Check expiry even while a page is idle; service calls also check it independently.
async function checkSession() {
  if (!sessionUser) return;
  const previousRole = sessionUser.role;
  if (api.isLive()) {
    try { await api.refreshSession(); await requireSession(); }
    catch (error) {
      if (await requireSession()) {
        const banner = document.querySelector("#scenario-banner");
        banner.hidden = false;
        banner.innerHTML = `<div class="notice warning">${esc(error.message)} Displayed live records have not refreshed.</div>`;
      }
      return;
    }
    // Identity-change subscriptions apply new permissions; do not render twice.
    return;
  }
  if (await requireSession()) {
    if (previousRole !== sessionUser.role) {
      dirty = false;
      await refresh();
    }
  }
}
window.addEventListener("focus", checkSession);
setInterval(checkSession, 30000);
document.querySelector("#live-refresh").onclick = async () => {
  if (!(await canLeave())) return;
  try {
    await api.refreshSession();
    if (await requireSession()) { dirty = false; await refresh(); }
  } catch (error) {
    if (await requireSession()) toast(error.message, "error");
  }
};
try {
  await api.initialize();
  if (await api.getSession()) await navigate(location.hash.slice(1) || "overview", {}, true);
  else showLogin();
} catch (error) { showLogin(error.message || "Could not complete live sign in. Please try again."); }
