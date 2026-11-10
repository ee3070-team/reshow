import {
  esc,
  icon,
  badge,
  toast,
  formatDate,
  formatTime,
  duration,
  metric,
  filterFields,
  readFilters,
  dateKey,
  activityNotes,
  confirmAction,
} from "../ui.js";

// Use the same clipped display periods in the preview and CSV export.
function displayPeriod(assignment, report) {
  const start = new Date(
    report.filters.start + "T00:00:00+08:00",
  ).toISOString();
  const end = new Date(
    Date.parse(report.filters.end + "T00:00:00+08:00") + 86400000,
  ).toISOString();
  return {
    start: assignment.start > start ? assignment.start : start,
    end: [assignment.end || report.createdAt, end, report.createdAt].sort()[0],
  };
}

export async function render(container, ctx) {
  const live = ctx.api.getMode?.() === "live";
  const snapshot = await ctx.api.getSnapshot({ include: ["exhibits", "reports"] });
  const { exhibits, reports } = snapshot;
  let records = reports,
    current = records[0] || null;
  if (live && current) current = await ctx.api.getReport(current.id);
  const today = dateKey(snapshot.now);
  const hasIncomingFilters = Object.keys(ctx.params).length > 0;
  if (hasIncomingFilters) current = null;
  const filters = {
    showcaseId: snapshot.showcase.id,
    showcaseName: snapshot.showcase.name,
    start: ctx.params.start || current?.filters.start || (live ? today : "2026-10-01"),
    end: ctx.params.end || current?.filters.end || (live ? today : "2026-10-06"),
    exhibitId: ctx.params.exhibitId || current?.filters.exhibitId || "",
  };
  container.innerHTML = `<div class="stack"><div class="reports-columns"><section class="card report-settings"><h2 class="section-title">${icon("report")}Create report</h2><form data-report-form>${filterFields(exhibits, filters)}<hr><h3>Include in report</h3><label class="check-row"><input type="checkbox" name="section" value="history" checked>Exhibit display history</label><label class="check-row"><input type="checkbox" name="section" value="activity" checked>Proximity activity</label><label class="check-row"><input type="checkbox" name="section" value="environment" checked>Environmental warnings</label><label class="check-row"><input type="checkbox" checked disabled>Missing-data information</label><div class="notice info">${icon("info")}Data completeness is always included.</div><span data-report-error class="field-error" role="alert"></span><button class="button primary" type="submit">Generate preview</button><p class="muted small-text" style="margin-top:12px">${live ? "Up to 31 inclusive HKT dates; current-day totals use a captured cutoff." : "Sample records: 1–6 Oct 2026."} Proximity activity does not identify individual visitors.</p></form></section><section class="card report-preview" data-preview></section></div><section class="card recent-reports"><h2 class="section-title">${icon("report")}Recent reports</h2><div data-recent></div></section></div>`;
  const form = container.querySelector("[data-report-form]"),
    preview = container.querySelector("[data-preview]");
  function syncFormToReport() {
    if (!current) return;
    for (const [name, value] of Object.entries(current.filters)) {
      if (form.elements[name]) form.elements[name].value = value;
    }
    form.querySelectorAll('[name="section"]').forEach((input) => {
      input.checked = current.sections.includes(input.value);
    });
  }
  function drawPreview() {
    if (!current) {
      preview.innerHTML = `<div class="report-empty empty">${icon("report")}<h3>Your exhibition, in perspective</h3><p>Choose a period and generate a report preview.</p><p class="small-text" style="margin-top:10px">Exports use the generated report’s recorded data.</p></div>`;
      return;
    }
    const r = current,
      d = r.data,
      s = d.summary,
      selected = r.sections;
    const missing = d.intervals.filter((i) => i.availability === "missing");
    const warnings = d.events.filter(
      (e) =>
        ["temperature", "brightness"].includes(e.type) &&
        e.status === "warning",
    );
    const start = new Date(r.filters.start + "T00:00:00+08:00").toISOString();
    const end = new Date(
      Date.parse(r.filters.end + "T00:00:00+08:00") + 86400000,
    ).toISOString();
    preview.innerHTML = `<p class="print-only">ReShow · ${live ? "API report" : "DEMO DATA · Simulated exhibition report"} · Generated ${esc(formatDate(r.createdAt))}, ${esc(formatTime(r.createdAt))}</p><div class="card-head"><h2>${icon("report")} Report preview</h2>${badge(live ? (r.saved ? "Saved API snapshot" : "API preview · unsaved") : "Demo report", "info")}</div><h2>${esc(r.title || snapshot.showcase.exhibition || "Exhibition report")}</h2><p class="report-meta">${esc(r.filters.showcaseId)} · ${esc(formatDate(start))} – ${esc(formatDate(r.filters.end + "T00:00:00+08:00"))} · HKT</p><p class="report-meta">${esc(r.filters.exhibitId ? r.exhibitNames[r.filters.exhibitId] : "All exhibits")} · ${esc(r.id)}</p>
    ${selected.includes("activity") ? `<div class="report-summary" style="grid-template-columns:repeat(${selected.includes("environment") ? 3 : 2},1fr)">${metric("Proximity events", (live || s.expectedIntervals) ? (s.proximityEvents ?? "—") : "—", "Recorded events", "activity")}${metric("Occupied duration", s.occupiedSeconds == null || (!live && !s.expectedIntervals) ? "—" : duration(s.occupiedSeconds), live ? "Through captured cutoff" : "Completed intervals", "clock")}${selected.includes("environment") ? metric("Warnings", s.warnings, "Environmental events", "warning", "amber") : ""}</div>` : ""}
    ${
      selected.includes("history")
        ? `<section class="report-section"><h3>${icon("cube")}Exhibit display history</h3><table><thead><tr><th>Exhibit</th><th>Display period (HKT)</th></tr></thead><tbody>${
            d.assignments.length
              ? d.assignments
                  .map((a) => {
                    const period = displayPeriod(a, r);
                    const from = period.start,
                      to = period.end;
                    return `<tr><td>${esc(r.exhibitNames[a.exhibitId] || a.exhibitId)}</td><td>${esc(formatDate(from))}, ${esc(formatTime(from))}<br>to ${esc(formatDate(to))}, ${esc(formatTime(to))}${!a.end ? " · ongoing at generation" : ""}</td></tr>`;
                  })
                  .join("")
              : '<tr><td colspan="2">No display records in this period.</td></tr>'
          }</tbody></table></section>`
        : ""
    }
    ${selected.includes("environment") ? `<section class="report-section"><h3>${icon("temperature")}Environmental warnings</h3><table><tbody><tr><td>Temperature</td><td>${new Set(warnings.filter((e) => e.type === "temperature").map(e => e.sourceId || e.id)).size} warning events</td></tr><tr><td>Relative brightness</td><td>${new Set(warnings.filter((e) => e.type === "brightness").map(e => e.sourceId || e.id)).size} warning events</td></tr></tbody></table></section>` : ""}
    ${activityNotes(d)}${r.profileContext?.note ? `<p class="notice info">${esc(r.profileContext.note)}</p>` : ""}<section class="report-section" ${live ? "hidden" : ""}><h3>${icon("database")}Data completeness</h3><p class="small-text">${s.expectedIntervals ? `${s.recordedIntervals} of ${s.expectedIntervals} completed intervals recorded (${s.coveragePercent}%). ${s.missingIntervals} missing intervals.` : "No completed observation intervals are available for this selection. Coverage cannot be determined."}</p>${missing.length ? `<div class="table-wrap"><table><thead><tr><th>Missing interval (HKT)</th><th>Availability</th></tr></thead><tbody>${missing.map((i) => `<tr><td>${esc(formatDate(i.start))}, ${esc(formatTime(i.start))} – ${esc(formatTime(i.end))}</td><td>Unknown activity</td></tr>`).join("")}</tbody></table></div>` : ""}<p class="notice info report-note">${icon("info")}Totals include recorded data only. Missing intervals are excluded, never treated as zero activity. The open hour and future hours are not yet included.</p><p class="muted small-text" style="margin-top:8px">Coverage describes retained demo intervals during exhibition hours (09:00–17:00), 1–6 Oct 2026. No claims are made about other dates.</p></section><div class="actions end report-actions">${live && !r.saved && ctx.api.can("save_reports") ? '<button class="button primary" data-save-report>Save new server snapshot</button>' : ""}<button class="button" data-print>${icon("report")}Print / Save as PDF</button><button class="button" data-csv>${icon("download")}Export CSV</button></div>`;
    preview.querySelector("[data-save-report]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      if (!(await confirmAction("Save report snapshot?", "The server captures current records again. The saved report may differ from this preview and will retain its own cutoff.", "Capture and save"))) return;
      button.disabled = true;
      try { current = await ctx.api.saveReport(current); records = await ctx.api.listReports(); drawPreview(); drawRecent(); toast("Server snapshot saved."); }
      catch (error) { toast(error.message, "error"); button.disabled = false; }
    });
    preview.querySelector("[data-print]").onclick = () => window.print();
    preview.querySelector("[data-csv]").onclick = () => exportCsv(current);
  }
  function drawRecent() {
    container.querySelector("[data-recent]").innerHTML = records.length
      ? `<div class="table-wrap"><table><thead><tr><th>Report</th><th>Period</th><th>Created (HKT)</th><th>Action</th></tr></thead><tbody>${records.map((r) => `<tr><td>School exhibition ${badge(live ? "Saved API" : "Demo", "info")}<br><small>${esc(r.id)}</small></td><td>${esc(r.filters.start)} – ${esc(r.filters.end)}</td><td>${esc(formatDate(r.createdAt))}, ${esc(formatTime(r.createdAt))}</td><td><button class="text-button" data-open-report="${esc(r.id)}">Open ${icon("arrow")}</button></td></tr>`).join("")}</tbody></table></div>`
      : '<p class="empty">No reports generated yet. Your generated reports will appear here.</p>';
    container.querySelectorAll("[data-open-report]").forEach(
      (b) =>
        (b.onclick = async () => {
          try {
          current = live ? await ctx.api.getReport(b.dataset.openReport) : records.find((r) => r.id === b.dataset.openReport);
          syncFormToReport();
          drawPreview();
          preview.scrollIntoView({ behavior: "smooth", block: "start" });
          } catch (error) { toast(error.message, "error"); }
        }),
    );
  }
  form.onsubmit = async (e) => {
    e.preventDefault();
    const button = form.querySelector("[type=submit]");
    try {
      const f = readFilters(form);
      form.querySelector("[data-report-error]").textContent = "";
      button.disabled = true;
      button.textContent = "Generating preview…";
      current = await ctx.api.generateReport(
        f,
        [...form.querySelectorAll("[name=section]:checked")].map(
          (i) => i.value,
        ),
      );
      records = await ctx.api.listReports();
      drawPreview();
      drawRecent();
      toast(live ? "API preview generated. Saving requires a separate server capture." : "Demo report generated from the selected records.");
    } catch (error) {
      form.querySelector("[data-report-error]").textContent = error.message;
    } finally {
      button.disabled = false;
      button.textContent = "Generate preview";
    }
  };
  form.addEventListener("change", () => {
    if (current) {
      const note =
        preview.querySelector("[data-stale-preview]") ||
        document.createElement("p");
      note.dataset.stalePreview = "";
      note.className = "notice warning no-print";
      note.textContent =
        "Report options changed. Generate a new preview to apply them; exports still use the report shown.";
      preview.prepend(note);
    }
  });
  syncFormToReport();
  drawPreview();
  drawRecent();
}

export function buildCsv(report) {
  const r = report,
    d = r.data,
    s = d.summary;
  const rows = [
    [r.data.raw ? "ReShow API report" : "ReShow demo report", r.id],
    ["Generated at (UTC)", r.createdAt],
    ["Display timezone", "Asia/Hong_Kong (UTC+8)"],
    ["Showcase", r.filters.showcaseId],
    ["Period (HKT)", r.filters.start, r.filters.end],
    [
      "Exhibit",
      r.filters.exhibitId
        ? r.exhibitNames[r.filters.exhibitId]
        : "All exhibits",
    ],
    [],
    ["Data completeness"],
    ...(d.raw ? [] : [
      ["Recorded intervals", s.recordedIntervals],
      ["Missing intervals", s.missingIntervals],
      ["Expected completed intervals", s.expectedIntervals],
    ]),
    ["Coverage percent", s.coveragePercent ?? "Unavailable"],
    [
      "Note",
      d.raw ? "Recorded lower bounds where coverage is incomplete. Future time is excluded; unknown data is never zero." : "Missing intervals are excluded from totals, not treated as zero. Open and future hours are excluded. Coverage describes retained demo intervals only.",
    ],
    [],
  ];
  if (d.raw) {
    rows.push(["Report state", r.saved ? "Immutable saved snapshot" : "Unsaved API preview"], ["Effective cutoff (UTC)", d.evaluation?.effective_end || d.raw.evaluation?.effective_end || "Unavailable"], ["Totals interpretation", d.raw.summary?.totals_interpretation], ["Producer sources", (d.provenance?.sources || d.raw.provenance?.sources || []).join("; ")]);
    for (const [stream, c] of Object.entries(d.completeness || d.raw.data_completeness || {})) rows.push(["Stream completeness", stream, "coverage seconds", c.coverage_seconds, "missing seconds", c.missing_seconds, "fraction", c.coverage_fraction ?? "Unavailable", c.denominator_note || ""]);
    for (const [stream, periods] of Object.entries(d.missingData || d.raw.missing_data || {})) for (const period of periods) rows.push(["Missing period", stream, period.start, period.end]);
    for (const note of [...(d.limitations || d.raw.limitations || []), ...(d.notices || [])]) rows.push(["Limitation", note]);
    if (r.profileContext?.note) rows.push(["Frozen profile context", r.profileContext.note]);
    rows.push(["Display attribution quality", JSON.stringify(d.dataQuality || d.raw.data_quality || {})]);
  }
  if (r.sections.includes("activity"))
    rows.push(
      ["Proximity events", s.proximityEvents],
      ["Occupied seconds", s.occupiedSeconds],
      ["Proximity events are not unique visitor counts"],
      [],
      ["Activity intervals"],
      [
        "Start (UTC)",
        "End (UTC)",
        "Exhibit",
        "Availability",
        "Proximity events",
        "Occupied seconds",
      ],
      ...d.intervals.map((i) => [
        i.start,
        i.end,
        r.exhibitNames[i.exhibitId] || i.exhibitId,
        i.availability,
        i.events ?? "",
        i.occupiedSeconds ?? "",
      ]),
      [],
    );
  if (r.sections.includes("history"))
    rows.push(
      ["Exhibit display history"],
      [
        "Exhibit",
        "Display start within report (UTC)",
        "Display end within report (UTC)",
        "Ongoing at generation",
      ],
      ...d.assignments.map((a) => [
        r.exhibitNames[a.exhibitId] || a.exhibitId,
        displayPeriod(a, r).start,
        displayPeriod(a, r).end,
        a.end ? "No" : "Yes",
      ]),
      [],
    );
  if (r.sections.includes("environment"))
    rows.push(
      ["Environmental warnings"],
      ["Time (UTC)", "Exhibit", "Type", "Details"],
      ...d.events
        .filter(
          (e) =>
            ["temperature", "brightness"].includes(e.type) &&
            e.status === "warning",
        )
        .map((e) => [
          e.at,
          r.exhibitNames[e.exhibitId] || e.exhibitId,
          e.type,
          e.details,
        ]),
      [],
    );
  rows.push(
    ["Missing intervals"],
    ["Start (UTC)", "End (UTC)", "Exhibit"],
    ...d.intervals
      .filter((i) => i.availability === "missing")
      .map((i) => [i.start, i.end, r.exhibitNames[i.exhibitId] || i.exhibitId]),
  );
  // Escape both CSV delimiters and spreadsheet formula prefixes in user-entered fields.
  return (
    "\uFEFF" +
    rows
      .map((row) =>
        row
          .map((v) => {
            let text = String(v ?? "");
            if (/^[\s]*[=+@-]/.test(text)) text = "'" + text;
            return '"' + text.replaceAll('"', '""') + '"';
          })
          .join(","),
      )
      .join("\r\n")
  );
}
function exportCsv(report) {
  const blob = new Blob([buildCsv(report)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `ReShow-${report.id}.csv`;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast("CSV exported from the generated report.");
}
