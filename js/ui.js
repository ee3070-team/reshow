const paths = {
  home: "m3 10 9-7 9 7M5 9v12h5v-7h4v7h5V9",
  cube: "m12 2 9 5v10l-9 5-9-5V7Zm0 10 9-5M12 12 3 7m9 5v10",
  tag: "M3 12 12 3h8v8l-9 10-8-8ZM16 7h.01",
  activity: "M3 14h3v7H3Zm7-6h3v13h-3Zm7-6h3v19h-3Z",
  report: "M5 2h9l5 5v15H5Zm9 0v6h5M9 12h6m-6 4h6",
  users:
    "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8m6 10v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  clock: "M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  sun: "M12 1v2m0 18v2M1 12h2m18 0h2M4.2 4.2l1.4 1.4m12.8 12.8 1.4 1.4m0-15.6-1.4 1.4M5.6 18.4l-1.4 1.4M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0",
  temperature:
    "M9 14.5V5a3 3 0 0 1 6 0v9.5a5 5 0 1 1-6 0ZM12 7v10m2 2a2 2 0 1 1-4 0 2 2 0 0 1 4 0",
  lock: "M5 10h14v12H5ZM8 10V6a4 4 0 0 1 8 0v4m-4 5v3",
  check: "m5 12 4 4L19 6",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  edit: "m16 3 5 5-12 12-6 1 1-6ZM14 5l5 5",
  plus: "M12 4v16M4 12h16",
  scan: "M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M1 12h22",
  info: "M12 11v6m0-10h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  upload: "M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6",
  close: "m6 6 12 12M6 18 18 6",
  card: "M2 5h20v14H2ZM2 9h20M6 15h4",
  download: "M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4",
  warning: "m12 2 11 20H1Zm0 6v6m0 4h.01",
  display: "M2 3h20v15H2Zm6 19h8m-4-4v4M6 12l4-4 3 3 3-5 3 6",
  database:
    "M21 5c0 2-4 4-9 4S3 7 3 5s4-4 9-4 9 2 9 4ZM3 5v14c0 2 4 4 9 4s9-2 9-4V5M3 12c0 2 4 4 9 4s9-2 9-4",
};
export const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const icon = (name) =>
  `<svg class="icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="${paths[name] || paths.info}"/></svg>`;
export function formatTime(value) {
  if (!value) return "Not received";
  return (
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Hong_Kong",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value)) + " HKT"
  );
}
export function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Hong_Kong",
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}
export function dateKey(value) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Hong_Kong",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}
export const duration = (seconds) =>
  `${Math.floor((seconds || 0) / 3600)} h ${Math.floor(((seconds || 0) % 3600) / 60)} min`;
export const badge = (text, tone = "neutral") =>
  `<span class="badge ${esc(tone)}">${esc(text)}</span>`;
export function toast(text, tone = "success") {
  const region = document.querySelector("#toast-region");
  const el = document.createElement("div");
  el.className = `toast ${["error", "danger"].includes(tone) ? "error" : ""}`;
  el.textContent = text;
  const close = document.createElement("button");
  close.textContent = "×";
  close.setAttribute("aria-label", "Dismiss notification");
  close.onclick = () => el.remove();
  el.append(close);
  region.append(el);
  setTimeout(() => el.remove(), 6000);
}
export function showDialog(title, html, onMount) {
  const el = document.createElement("dialog");
  el.innerHTML = `<div class="dialog-header"><h2>${esc(title)}</h2><button class="dialog-close" aria-label="Close dialog">${icon("close")}</button></div><div class="dialog-body">${html}</div>`;
  document.body.append(el);
  el.querySelector(".dialog-close").onclick = () => el.close();
  el.addEventListener("click", (e) => {
    if (e.target === el) {
      const r = el.getBoundingClientRect();
      if (
        e.clientX < r.left ||
        e.clientX > r.right ||
        e.clientY < r.top ||
        e.clientY > r.bottom
      )
        el.close();
    }
  });
  el.addEventListener("close", () => el.remove(), { once: true });
  el.showModal();
  onMount?.(el);
  return el;
}
export function confirmAction(title, message, button = "Confirm") {
  return new Promise((resolve) => {
    let result = false;
    const d = showDialog(
      title,
      `<p>${esc(message)}</p><div class="actions"><button class="button" data-cancel>Cancel</button><button class="button primary" data-confirm>${esc(button)}</button></div>`,
      (d) => {
        d.querySelector("[data-cancel]").onclick = () => d.close();
        d.querySelector("[data-confirm]").onclick = () => {
          result = true;
          d.close();
        };
      },
    );
    d.addEventListener("close", () => resolve(result), { once: true });
  });
}
export function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    if (!file) return resolve("");
    if (
      !["image/jpeg", "image/png", "image/webp", "image/gif"].includes(
        file.type,
      )
    )
      return reject(new Error("Choose a PNG, JPEG, WebP, or GIF image."));
    if (file.size > 1500000)
      return reject(
        new Error("Choose an image smaller than 1.5 MB for demo storage."),
      );
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("The image could not be read."));
    reader.readAsDataURL(file);
  });
}
export function statusStrip(snapshot) {
  const s = snapshot.showcase;
  const connection = !s.lastReceivedAt
    ? "No connection data"
    : s.online
      ? "Online"
      : "Offline · readings stale";
  const localControl =
    s.localControl == null
      ? "Local-control state unavailable"
      : s.localControl
        ? "Local control active"
        : "Local control unavailable";
  return `<div class="status-strip"><span class="${s.online ? "teal" : "amber"}"><i class="status-dot ${s.online ? "" : "offline"}"></i><strong>${connection}</strong></span><span>${esc(s.name)}</span><span>Last received: ${esc(formatTime(s.lastReceivedAt))}</span><span>${localControl}</span>${s.source ? `<span>Reported source: ${esc(s.source)}</span>` : ""}${s.freshness ? `<span>Measurements: ${s.measurementStale == null ? "unavailable" : s.measurementStale ? "stale" : "fresh"} · Contact age ${esc(s.freshness.contact_age_seconds ?? "unknown")} s</span>` : ""}</div>`;
}
export function metric(label, value, caption, ico = "activity", tone = "") {
  return `<div class="card metric"><span class="${tone}">${icon(ico)}</span><div><p class="metric-label">${esc(label)}</p><div class="metric-value ${tone}">${esc(value)}</div><p class="metric-caption">${esc(caption)}</p></div></div>`;
}
export function heatmap(
  intervals,
  metricName = "occupiedSeconds",
  compact = false,
) {
  if (!intervals.length)
    return '<div class="empty">No activity intervals match these filters.</div>';
  const days = [...new Set(intervals.map((i) => dateKey(i.start)))];
  const hourOf = (i) => Number(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Hong_Kong", hour: "2-digit", hourCycle: "h23" }).format(new Date(i.start)));
  const hours = [...new Set(intervals.map(hourOf))].sort((a, b) => a - b);
  const map = new Map(
    intervals.map((i) => [
      `${dateKey(i.start)}-${new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Hong_Kong", hour: "2-digit", hourCycle: "h23" }).format(new Date(i.start))}`,
      i,
    ]),
  );
  let html = `<div class="heatmap-scroll"><div class="heatmap ${compact ? "compact" : ""}" style="grid-template-columns:${compact ? "" : "64px "}repeat(${hours.length}, minmax(25px, 1fr))">`;
  if (!compact)
    html +=
      "<span></span>" +
      Array.from(
        { length: hours.length },
        (_, x) => `<span class="heatmap-label">${hours[x]}:00</span>`,
      ).join("");
  for (const day of days) {
    if (!compact)
      html += `<span class="heatmap-label">${esc(new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "Asia/Hong_Kong" }).format(new Date(day + "T00:00:00+08:00")))}</span>`;
    for (const h of hours) {
      const i = map.get(`${day}-${String(h).padStart(2, "0")}`);
      if (!i) {
        html += "<span></span>";
        continue;
      }
      const val = i[metricName];
      const cls =
        i.availability === "recorded" && val != null
          ? `level-${val === 0 ? 0 : Math.min(5, Math.max(1, Math.ceil(val / (metricName === "events" ? 3 : 420))))}`
          : esc(i.availability || "missing");
      const quantity = val == null ? "Activity unknown" : metricName === "events" ? val + " proximity events" : (val / 60).toFixed(1) + " occupied minutes (" + val + " s)";
      const info = `${formatDate(i.start)}, ${formatTime(i.start)}–${formatTime(i.end)} · ${quantity} · ${(i.status || i.availability).replaceAll("_", " ")}${i.availability === "partial" ? " (recorded lower bound)" : ""}`;
      html += `<button type="button" class="heatmap-cell ${cls}" title="${esc(info)}" aria-label="${esc(info)}" data-interval-detail="${esc(info)}"></button>`;
    }
  }
  if (compact)
    html += Array.from(
      { length: hours.length },
      (_, x) => `<span class="heatmap-label">${hours[x]}:00</span>`,
    ).join("");
  html +=
    '</div></div><div class="heatmap-legend"><span><i class="legend-swatch zero"></i>Confirmed zero</span><span><i class="legend-swatch"></i>Low</span><span><i class="legend-swatch high"></i>High</span><span><i class="legend-swatch missing"></i>Missing / partial</span><span><i class="legend-swatch future"></i>Future / outside scope</span></div>';
  return html;
}
export function bindHeatmap(container) {
  container
    .querySelectorAll("[data-interval-detail]")
    .forEach(
      (el) =>
        (el.onclick = () =>
          showDialog(
            "Activity interval",
            `<p>${esc(el.dataset.intervalDetail)}</p><p class="muted small-text" style="margin-top:12px">Proximity events are not unique visitor counts.</p>`,
          )),
    );
}
export function filterFields(exhibits, filters) {
  return `<label class="field">Showcase<select name="showcaseId" aria-label="Showcase"><option value="${esc(filters.showcaseId || "case-01")}">${esc(filters.showcaseName || filters.showcaseId || "Case 01")}</option></select></label><label class="field">From<input name="start" type="date" value="${esc(filters.start)}" required></label><label class="field">To<input name="end" type="date" value="${esc(filters.end)}" required></label><label class="field">Exhibit<select name="exhibitId" aria-label="Exhibit"><option value="">All exhibits</option>${exhibits.map((e) => `<option value="${esc(e.id)}" ${filters.exhibitId === e.id ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select></label>`;
}
export function readFilters(form) {
  const f = Object.fromEntries(new FormData(form));
  if (!f.start || !f.end) throw new Error("Choose a start and end date.");
  if (f.start > f.end)
    throw new Error("The end date must be on or after the start date.");
  if ((new Date(f.end) - new Date(f.start)) / 86400000 >= 31)
    throw new Error("Choose at most 31 inclusive calendar days.");
  return {
    showcaseId: f.showcaseId,
    start: f.start,
    end: f.end,
    exhibitId: f.exhibitId || "",
  };
}

// Shared evidence notes keep Activity, Overview, reports and print truthful.
export function activityNotes(data) {
  if (!data.raw) return "";
  const r = data.raw;
  const evaluation = data.evaluation || r.evaluation;
  const completeness = data.completeness || r.data_completeness || {};
  return `<div class="notice info evidence-notes"><div><strong>API evidence · ${esc((data.provenance?.sources || r.provenance?.sources || []).join(", ") || "source unspecified")}</strong>${(data.provenance?.contains_demo_data || r.provenance?.contains_demo_data) ? " · Contains synthetic producer data" : ""}<p>Totals: ${esc((r.summary?.totals_interpretation || "unavailable").replaceAll("_", " "))}. Missing observations remain unknown.</p>${evaluation ? `<p>Evaluated through ${esc(formatDate(evaluation.effective_end))}, ${esc(formatTime(evaluation.effective_end))} · ${esc(evaluation.status)}</p>` : ""}${Object.entries(completeness).map(([stream, c]) => `<p>${esc(stream)} coverage: ${c.coverage_fraction == null ? "unavailable" : (c.coverage_fraction * 100).toFixed(1) + "%"} · ${esc(c.missing_seconds)} s missing${c.denominator_note ? " · " + esc(c.denominator_note) : ""}</p>`).join("")}${Object.entries(data.missingData || r.missing_data || {}).map(([stream, periods]) => periods.length ? `<details><summary>${esc(stream)}: ${periods.length} missing periods</summary>${periods.map(p => `<p>${esc(formatDate(p.start))}, ${esc(formatTime(p.start))} – ${esc(formatDate(p.end))}, ${esc(formatTime(p.end))}</p>`).join("")}</details>` : "").join("")}${r.summary?.unknown_assignment_seconds != null ? `<p>Unknown assignment: ${esc(r.summary.unknown_assignment_seconds)} s · Ambiguous assignment: ${esc(r.summary.ambiguous_assignment_seconds ?? 0)} s · Known other-exhibit time: ${esc(r.summary.outside_scope_seconds ?? 0)} s (excluded)</p>` : ""}${[...(data.limitations || r.limitations || []), ...(data.notices || []), r.data_quality?.display_history_note].filter(Boolean).map(note => `<p>${esc(note)}</p>`).join("")}</div></div>`;
}
