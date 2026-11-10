import {
  esc,
  icon,
  formatTime,
  formatDate,
  dateKey,
  badge,
  toast,
  statusStrip,
  metric,
  heatmap,
  bindHeatmap,
  confirmAction,
  activityNotes,
} from "../ui.js";

export async function render(container, ctx) {
  let disposed = false, period = "today", drawing = null, queued = false;
  let cachedActivity = null;
  let refreshError = "", errorRevision = 0;
  const emptyActivity = () => ({ intervals: [], events: [], summary: { proximityEvents: null } });
  function showRefreshError(error) {
    if (disposed) return;
    refreshError = `Automatic refresh failed: ${error.message} Displayed records may be out of date. Retrying automatically.`;
    errorRevision++;
    const notice = container.querySelector("[data-refresh-status]");
    if (!disposed && notice) {
      notice.hidden = false;
      notice.textContent = refreshError;
    }
  }
  // Finish and paint each read before processing one coalesced invalidation.
  // Increasing a sequence on every notification could starve a slow render.
  function draw() {
    if (disposed) return Promise.resolve();
    if (drawing) { queued = true; return drawing; }
    drawing = (async () => {
      do {
        queued = false;
        await readAndPaint();
      } while (queued && !disposed);
    })().finally(() => { drawing = null; });
    return drawing;
  }
  async function readAndPaint() {
    const startedErrorRevision = errorRevision;
    const snapshot = await ctx.api.getSnapshot({ include: ["exhibits", "operations"] });
    if (disposed) return;
    const s = snapshot.showcase, today = dateKey(snapshot.now), selectedPeriod = period;
    const live = ctx.api.getMode?.() === "live";
    const periodStart = live ? dateKey(new Date(Date.parse(snapshot.now) - 6 * 86400000).toISOString()) : "2026-10-01";
    const key = `${s.id}:${selectedPeriod}:${today}`;
    const previous = cachedActivity?.key === key ? cachedActivity : null;
    // Measurements and acknowledged device state do not wait for analytics.
    paint(snapshot, previous?.data || emptyActivity(), previous?.todayData || emptyActivity(), "", true);
    let activityError = "";
    const readActivity = async (filters, retained) => {
      try { return await ctx.api.getActivity(filters); }
      catch (error) { activityError = error.message; return retained || emptyActivity(); }
    };
    const data = await readActivity({ showcaseId: s.id, start: selectedPeriod === "today" ? today : periodStart, end: today, exhibitId: "" }, previous?.data);
    const todayData = selectedPeriod === "today" ? data : await readActivity({ showcaseId: s.id, start: today, end: today }, previous?.todayData);
    if (disposed || selectedPeriod !== period) return;
    cachedActivity = { key, data, todayData };
    if (!activityError && startedErrorRevision === errorRevision) refreshError = "";
    paint(snapshot, data, todayData, activityError, false);
  }
  function paint(snapshot, data, todayData, activityError, activityLoading) {
    const canOperate = ctx.api.can("operate"), live = ctx.api.getMode?.() === "live";
    const s = snapshot.showcase, e = snapshot.exhibits.find((item) => item.id === s.exhibitId);
    const today = dateKey(snapshot.now);
    const periodStart = live ? dateKey(new Date(Date.parse(snapshot.now) - 6 * 86400000).toISOString()) : "2026-10-01";
    const pending = snapshot.operations.filter(
      (o) => o.type === "lighting" && ["pending", "recovery_pending"].includes(o.status),
    );
    const lastLighting = snapshot.operations
      .filter((o) => o.type === "lighting")
      .sort((a, b) => Date.parse(a.requestedAt) - Date.parse(b.requestedAt)).at(-1);
    const latestLabel = snapshot.operations
      .filter((o) => o.type === "label")
      .sort((a, b) => Date.parse(a.requestedAt) - Date.parse(b.requestedAt)).at(-1);
    const label = s.label;
    const outBrightness =
        e &&
        s.brightness != null &&
        (s.brightness < e.brightnessMin || s.brightness > e.brightnessMax),
      outTemperature =
        e &&
        s.temperature != null &&
        (s.temperature < e.temperatureMin || s.temperature > e.temperatureMax);
    const stale = s.measurementStale || !s.online ? " · stale" : "";
    const status = (value) =>
      value == null ? "No reading" : String(value) + stale;
    container.innerHTML = `<div class="stack"><div class="notice warning" data-refresh-status role="status" ${refreshError ? "" : "hidden"}>${esc(refreshError)}</div>${statusStrip(snapshot)}
      ${activityLoading ? '<div class="notice info" role="status">Refreshing activity… Previous activity, if shown, is awaiting an update.</div>' : ""}
      ${canOperate ? "" : '<div class="notice info">Viewer access · Read-only readings, exhibits and labels.</div>'}
      ${outBrightness || outTemperature ? `<div class="notice warning">${icon("warning")}<strong>${outBrightness ? "Brightness" : "Temperature"} outside profile limits${stale}</strong><span>Warning persistence: ${esc(e.warningSeconds)} s</span><button class="text-button" data-alert>View alert ${icon("arrow")}</button></div>` : ""}
      <div class="metrics">${metric("Temperature", s.temperature == null ? "—" : `${s.temperature} °C`, e && s.temperature != null ? `${!stale ? "" : "Stale · "}${outTemperature ? "Outside" : "Within"} range · ${e.temperatureMin}–${e.temperatureMax} °C` : "No reading available", "temperature", outTemperature ? "amber" : "")}${metric("Relative brightness", s.brightness == null ? "—" : `${s.brightness} / 100`, e && s.brightness != null ? `${!stale ? "" : "Stale · "}${outBrightness ? "Outside" : "Within"} range · ${e.brightnessMin}–${e.brightnessMax}` : "No reading available", "sun", outBrightness ? "amber" : "")}${metric("Detection zone", s.zone || "—", s.zone ? `Last received state${stale}` : "No reading available", "users")}${metric("Proximity events today", (live || todayData.summary.expectedIntervals) ? (todayData.summary.proximityEvents ?? "—") : "—", "Recorded events, not unique visitors", "activity")}</div>
      <div class="overview-middle">
        <section class="card"><h2 class="section-title">${icon("cube")}Current exhibit</h2>${e ? `<div class="exhibit-summary">${live && !e.image ? `<div class="empty" aria-label="No exhibit image">${icon("cube")}<small>No image</small></div>` : `<img src="${esc(e.image || "assets/vase.svg")}" alt="Illustration of ${esc(e.name)}">`}<div><h3>${esc(e.name)}</h3><p>Tag: ${esc(e.tagId)}</p>${badge("Profile assigned", "success")}</div></div><div class="actions">${canOperate ? `<button class="button" data-edit>${icon("edit")}Edit profile</button>` : ""}<button class="button" data-change>${icon("arrow")}${canOperate ? "Change exhibit" : "View exhibits"}</button></div>` : '<div class="empty">No exhibit assigned.</div><button class="button" data-change>Open exhibit library</button>'}</section>
        <section class="card"><h2 class="section-title">${icon("display")}Electronic label</h2>${label ? `<div class="epaper"><div class="epaper-inner">${label.content.image ? `<img src="${esc(label.content.image)}" alt="Label image">` : ""}<div><h3>${esc(label.content.title)}</h3><p>${esc(label.content.collection)}</p></div></div></div><div class="label-status">${icon("check")} Last update acknowledged<br><span class="muted">${esc(formatTime(label.acknowledgedAt))}</span></div>${label.exhibitId !== s.exhibitId ? '<p class="notice warning">The displayed label belongs to the previous exhibit. A new label update is needed.</p>' : ""}` : '<div class="empty">No acknowledged label.</div>'}${latestLabel && latestLabel.status !== "acknowledged" ? `<p class="small-text" style="margin-bottom:10px">${badge(["pending", "recovery_pending"].includes(latestLabel.status) ? "Pending / recovery" : latestLabel.status === "recovered" ? "Recovered request" : "Failed update", latestLabel.status)} ${esc(latestLabel.error || "Waiting for the showcase")}</p>` : ""}<button class="button full" data-label ${e ? "" : "disabled"}>${icon(canOperate ? "edit" : "display")}${canOperate ? "Edit label" : "View label"}</button><p class="muted small-text" style="margin-top:7px">Illustrative e-paper layout</p></section>
        <section class="card"><h2 class="section-title">${icon("sun")}Lighting & access</h2><div class="segmented" role="group" aria-label="Lighting mode">${["Auto", "On", "Off"].map((mode) => `<button type="button" data-mode="${mode}" aria-pressed="${s.lightingMode === mode}" class="${s.lightingMode === mode ? "active" : ""}" ${!canOperate || pending.length || !e ? "disabled" : ""}>${mode}</button>`).join("")}</div><p class="device-detail">Confirmed mode: <strong>${esc(status(s.lightingMode))}</strong></p><p class="device-detail">Light output: <strong>${esc(status(s.lightingOutput))}</strong></p>${live ? "" : '<p class="muted small-text">Clear-zone delay: 30 s</p>'}${pending.length ? `<p class="notice warning" style="margin-top:10px">Requested ${esc(pending.at(-1).payload.mode)} · Pending acknowledgement</p>` : lastLighting?.status === "failed" ? `<p class="notice error" style="margin-top:10px">Previous lighting request failed: ${esc(lastLighting.error)}</p>` : ""}<div class="latch">${icon("lock")}<div>Latch: <strong>${esc(status(s.latch))}</strong><p class="muted">Staff card required</p></div></div></section>
      </div>
      <div class="overview-bottom"><section class="card"><div class="card-head"><h2>${icon("activity")}Activity over time</h2><select class="compact-range" aria-label="Overview activity period" data-period><option value="today" ${period === "today" ? "selected" : ""}>Today</option><option value="all" ${period === "all" ? "selected" : ""}>${live ? "Last 7 days" : "1–6 Oct"}</option></select></div><p class="small-text">${live ? "Occupied duration per hour through captured cutoff" : "Occupied duration per completed hour"}</p>${heatmap(data.intervals, "occupiedSeconds", period === "today")}<p class="footnote">Anonymous proximity activity; no visitor identification. ${live ? "Partial data is a lower bound; future time is excluded." : "Open and future hours are not yet counted."}</p></section><section class="card"><div class="card-head"><h2>${icon("clock")}Recent activity</h2></div>${
        data.events.length
          ? data.events
              .slice(0, 4)
              .map(
                (event) =>
                  `<div class="recent-event"><time>${esc(formatTime(event.at).replace(" HKT", ""))}</time><i class="status-dot ${event.status === "warning" || event.status === "failed" ? "offline" : ""}"></i><span>${esc(event.type === "proximity" ? "Proximity event" : event.type.charAt(0).toUpperCase() + event.type.slice(1) + " · " + event.status)}</span></div>`,
              )
              .join("")
          : '<p class="empty">No events recorded.</p>'
      }<div class="recent-footer"><button class="text-button" data-events>View all events ${icon("arrow")}</button></div></section></div></div>`;
    if (activityError) container.querySelector(".stack").insertAdjacentHTML("afterbegin", `<div class="notice warning">Activity unavailable: ${esc(activityError)}. Previously loaded activity, if shown, may be stale. Retrying automatically. Readings below retain their own received state.</div>`);
    if (live) {
      container.querySelector(".stack").insertAdjacentHTML("beforeend", activityNotes(data));
      for (const note of snapshot.notices || snapshot.limitations || []) container.querySelector(".stack").insertAdjacentHTML("afterbegin", `<div class="notice warning">${esc(note)}</div>`);
    }
    container
      .querySelector("[data-edit]")
      ?.addEventListener("click", () =>
        ctx.navigate("exhibits", { exhibitId: e.id, edit: true }),
      );
    container.querySelector("[data-change]").onclick = () =>
      ctx.navigate("exhibits");
    container.querySelector("[data-label]").onclick = () =>
      ctx.navigate("labels", { exhibitId: e?.id });
    container.querySelector("[data-events]").onclick = () =>
      ctx.navigate("activity", {
        start: period === "today" ? today : periodStart,
        end: today,
      });
    container.querySelector("[data-alert]")?.addEventListener("click", () =>
      ctx.navigate("activity", {
        start: today,
        end: today,
        eventType: outBrightness ? "brightness" : "temperature",
      }),
    );
    container.querySelector("[data-period]").onchange = (event) => {
      period = event.target.value;
      draw().catch(showRefreshError);
    };
    container.querySelectorAll("[data-mode]").forEach(
      (button) =>
        (button.onclick = async () => {
          if (!ctx.api.can("operate")) return;
          if (!(await confirmAction("Request lighting change?", `Request ${button.dataset.mode} lighting. Confirmed state changes only after a producer acknowledgement.`, "Request change"))) return;
          container
            .querySelectorAll("[data-mode]")
            .forEach((b) => (b.disabled = true));
          try {
            await ctx.api.setLighting(button.dataset.mode);
            toast(`${live ? "Live" : "Demo"} lighting request queued. Waiting for acknowledgement.`);
            await draw();
          } catch (error) {
            toast(error.message, "error");
            await draw();
          }
        }),
    );
    bindHeatmap(container);
  }
  await draw();
  const unsubscribe = ctx.api.subscribe((event) => {
    if (event?.error) { showRefreshError(event.error); return; }
    return draw().catch(showRefreshError);
  });
  return () => {
    disposed = true;
    unsubscribe();
  };
}
