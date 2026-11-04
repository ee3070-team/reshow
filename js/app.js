import { api } from "./services.js";
import { initializeStaffLogin } from "./live-accounts.js";
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
let current = "overview",
  params = {},
  cleanup = null,
  dirty = false,
  renderId = 0;
const content = document.querySelector("#page-content");
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
  if (!pages[tab]) tab = "overview";
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
  const id = ++renderId;
  cleanup?.();
  cleanup = null;
  const page = pages[current];
  document.title = `ReShow · ${page.title}`;
  document.querySelector("#page-title").textContent = page.title;
  document.querySelector("#page-subtitle").textContent = page.subtitle;
  document.querySelector("#page-mode").textContent = page.live ? "Live AWS showcase" : "Local showcase demo";
  document.querySelector("#demo-controls").hidden = Boolean(page.live);
  document.querySelector("#report-shortcut").hidden = ![
    "overview",
    "activity",
  ].includes(current);
  document.querySelectorAll("[data-page]").forEach((a) => {
    a.classList.toggle("active", a.dataset.page === current);
    if (a.dataset.page === current) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  content.innerHTML =
    `<div class="loading-state" role="status"><div class="spinner"></div><span>${page.live ? "Opening live showcase…" : "Loading demo records…"}</span></div>`;
  try {
    if (page.live) {
      document.querySelector("#demo-date").textContent = "HKT (UTC+8)";
      document.querySelector("#scenario-banner").hidden = true;
    } else {
      const snap = await api.getSnapshot();
      if (id !== renderId) return;
      document.querySelector("#demo-date").textContent = `${formatDate(snap.now)} · Demo clock`;
      updateBanner(snap);
    }
    const stage = document.createElement("div");
    content.replaceChildren(stage);
    const off = await page.render(stage, {
      api,
      navigate,
      params,
      refresh,
      setDirty: (value) => {
        if (id === renderId) dirty = value;
      },
    });
    if (id === renderId) cleanup = off;
    else off?.();
  } catch (error) {
    if (id !== renderId) return;
    content.innerHTML = `<div class="card empty"><h3>Could not load demo records</h3><p>${esc(error.message)}</p><button id="retry-page" class="button" style="margin-top:16px">Try again</button></div>`;
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
  const message = messages[mode] || snap.storageWarning;
  banner.hidden = !message;
  banner.innerHTML = message
    ? `<div class="notice ${mode === "offline" || mode === "failed" ? "warning" : "info"}">${icon("info")}${esc(message)}</div>`
    : "";
}
document.querySelector("#demo-controls").onclick = handleAction(async () => {
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
// Keep the demo clock and storage warnings current without replacing page editors.
api.subscribe(() => {
  updateHeader().catch(reportError);
});
async function updateHeader() {
  if (pages[current].live) return;
  const snapshot = await api.getSnapshot();
  updateBanner(snapshot);
  document.querySelector("#demo-date").textContent =
    `${formatDate(snapshot.now)} · Demo clock`;
}
window.addEventListener("popstate", handleAction(async (e) => {
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
initializeStaffLogin()
  .then(() => navigate(location.hash.slice(1) || "overview", {}, true))
  .catch(reportError);
