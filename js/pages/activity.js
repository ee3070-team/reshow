import {
  esc,
  icon,
  badge,
  toast,
  formatDate,
  formatTime,
  duration,
  metric,
  heatmap,
  bindHeatmap,
  filterFields,
  readFilters,
  dateKey,
  activityNotes,
} from "../ui.js";

export async function render(container, ctx) {
  const snapshot = await ctx.api.getSnapshot({ include: ["exhibits"] });
  const exhibits = snapshot.exhibits;
  const live = ctx.api.getMode?.() === "live";
  const today = dateKey(snapshot.now);
  let filters = {
    showcaseId: snapshot.showcase.id,
    showcaseName: snapshot.showcase.name,
    start: ctx.params.start || (live ? today : "2026-10-01"),
    end: ctx.params.end || (live ? today : "2026-10-06"),
    exhibitId: ctx.params.exhibitId || "",
  };
  let eventType = ctx.params.eventType || "",
    measure = "occupiedSeconds",
    limit = 12,
    disposed = false,
    sequence = 0;
  container.innerHTML = `<div class="stack"><form class="card filters" data-filters>${filterFields(exhibits, filters)}<button class="button primary" type="submit">Apply filters</button><span class="field-error" data-filter-error></span></form><div class="notice">${icon("info")} Proximity events indicate activity near the case, not unique visitors.</div><div data-results></div></div>`;
  const form = container.querySelector("[data-filters]"),
    results = container.querySelector("[data-results]");
  async function load() {
    const run = ++sequence;
    results.innerHTML =
      '<div class="loading-state" role="status"><div class="spinner"></div>Loading activity…</div>';
    try {
      const data = await ctx.api.getActivity(filters);
      if (disposed || run !== sequence) return;
      draw(data);
    } catch (error) {
      if (!disposed)
        results.innerHTML = `<div class="notice error">${esc(error.message)}</div>`;
    }
  }
  function draw(data) {
    const s = data.summary;
    const shown = data.events.filter((e) => !eventType || e.type === eventType);
    results.innerHTML = `<div class="stack"><div class="metrics">${metric("Proximity events", (live || s.expectedIntervals) ? (s.proximityEvents ?? "—") : "—", "Recorded events in selected period", "activity")}${metric("Occupied duration", s.occupiedSeconds == null || (!live && !s.expectedIntervals) ? "—" : duration(s.occupiedSeconds), live ? "Recorded duration through cutoff" : "Completed intervals only", "clock")}${metric("Environmental warnings", s.warnings, "Temperature and brightness", "warning", "amber")}${metric("Data coverage", s.coveragePercent == null ? "—" : `${s.coveragePercent}%`, live ? "Time-based proximity coverage" : `${s.missingIntervals} missing intervals · ${s.recordedIntervals}/${s.expectedIntervals} recorded`, "database")}</div>${activityNotes(data)}<div class="activity-columns"><section class="card"><div class="card-head"><h2>${icon("activity")}Activity heatmap</h2><select data-measure aria-label="Heatmap measure" style="width:180px"><option value="occupiedSeconds" ${measure === "occupiedSeconds" ? "selected" : ""}>Occupied duration</option><option value="events" ${measure === "events" ? "selected" : ""}>Event frequency</option></select></div><p class="small-text">${measure === "events" ? "Proximity events" : "Occupied minutes"} ${live ? "per hour through captured cutoff · API records" : "per completed hour · illustrative data"}</p>${heatmap(data.intervals, measure)}<p class="footnote">${live ? "Partial buckets show recorded lower bounds. Future time is outside evaluated coverage." : "Missing intervals are excluded from activity totals. The open hour and future hours are not counted yet."}</p></section><section class="card"><h2 class="section-title">${icon("clock")}Reading the activity</h2><div class="reading-item">${icon("activity")}<div><h3>Proximity events</h3><p>A new event starts when the detection zone becomes occupied.</p></div></div><div class="reading-item">${icon("clock")}<div><h3>Occupied duration</h3><p>Time with an object detected in the zone. It does not measure attention.</p></div></div><div class="reading-item">${icon("database")}<div><h3>Missing data</h3><p>No retained readings; activity is unknown. White outlined cells indicate confirmed zero activity.</p></div></div><p class="footnote">Groups or stationary objects may produce one continuous event.</p></section></div><section class="card"><div class="card-head"><h2>${icon("report")}Event log</h2><select data-event-type aria-label="Event type" style="width:195px"><option value="">All event types</option>${[
      ["proximity", "Proximity"],
      ["temperature", "Temperature warning"],
      ["brightness", "Brightness warning"],
      ["missing", "Missing data"],
      ["label", "Label update"],
      ["lighting", "Lighting change"],
      ["assignment", "Exhibit assignment"],
      ["card", "Card permission"],
    ]
      .map(
        ([value, title]) =>
          `<option value="${value}" ${value === eventType ? "selected" : ""}>${title}</option>`,
      )
      .join(
        "",
      )}</select></div><div class="table-wrap"><table><thead><tr><th>Time (HKT)</th><th>Event</th><th>Details</th><th>Status</th></tr></thead><tbody>${
      shown.length
        ? shown
            .slice(0, limit)
            .map(
              (e) =>
                `<tr><td class="nowrap">${esc(formatDate(e.at).replace(" 2026", ""))}, ${esc(formatTime(e.at).replace(" HKT", ""))}</td><td>${esc(e.type.charAt(0).toUpperCase() + e.type.slice(1))}</td><td>${esc(e.details)}</td><td>${badge(e.type === "missing" ? "Missing" : e.status, e.status === "warning" ? "warning" : e.status === "failed" ? "error" : e.status === "acknowledged" ? "success" : "neutral")}</td></tr>`,
            )
            .join("")
        : '<tr><td colspan="4" class="empty">No events match the selected filters.</td></tr>'
    }</tbody></table></div><div class="actions end" style="margin-top:12px"><span class="muted small-text">Showing ${Math.min(limit, shown.length)} of ${shown.length} records</span>${shown.length > limit ? '<button class="text-button" data-more>Show 30 more ' + icon("arrow") + "</button>" : ""}<button class="text-button" data-report>Generate report ${icon("arrow")}</button></div></section></div>`;
    results.querySelector("[data-measure]").onchange = (e) => {
      measure = e.target.value;
      draw(data);
    };
    results.querySelector("[data-event-type]").onchange = (e) => {
      eventType = e.target.value;
      limit = 12;
      draw(data);
    };
    results.querySelector("[data-more]")?.addEventListener("click", () => {
      limit += 30;
      draw(data);
    });
    results.querySelector("[data-report]").onclick = () =>
      ctx.navigate("reports", filters);
    bindHeatmap(results);
  }
  form.onsubmit = async (e) => {
    e.preventDefault();
    try {
      filters = readFilters(form);
      container.querySelector("[data-filter-error]").textContent = "";
      limit = 12;
      await load();
    } catch (error) {
      container.querySelector("[data-filter-error]").textContent =
        error.message;
    }
  };
  await load();
  const off = ctx.api.subscribe(() => load());
  return () => {
    disposed = true;
    off();
  };
}
