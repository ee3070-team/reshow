import { staffSession } from "../auth-session.js";
import { loadLiveShowcase, mergeLiveShowcase } from "../live-showcase.js";
import { createLiveRefresh } from "../live-refresh.js";
import { esc } from "../ui.js";

const value = (item) => item === null || item === undefined ? "Unknown" : String(item);
function dateTime(instant) {
  const date = new Date(instant);
  return instant && Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat("en-HK", {
    timeZone: "Asia/Hong_Kong", dateStyle: "medium", timeStyle: "medium",
  }).format(date) + " HKT" : "Unknown";
}
const cells = (items) => items.map((item) => `<td>${esc(value(item))}</td>`).join("");
function table(title, headings, rows, more = false) {
  return `<section class="card"><h2>${esc(title)}</h2>${rows.length ? `<div class="table-wrap"><table><thead><tr>${headings.map((heading) => `<th scope="col">${esc(heading)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${cells(row)}</tr>`).join("")}</tbody></table></div>` : '<p class="muted">No records available in this section.</p>'}${more ? '<p class="muted">Showing one page of up to 20 records. Your role may hide some entries; older permitted history is available through the staff API.</p>' : ""}</section>`;
}
const facts = (rows) => `<dl class="live-demo-facts">${rows.map(([title, detail]) => `<div><dt>${esc(title)}</dt><dd>${esc(value(detail))}</dd></div>`).join("")}</dl>`;

export function showcaseHTML(data, role) {
  if (data.missing) return '<div class="card empty"><h2>Demo showcase is not set up yet</h2><p>Create the fixture with the Python seed tool, then refresh this page.</p></div>';
  const s = data.showcase, status = s.device_status, label = s.confirmed_label;
  const summary = data.report?.summary;
  const stale = s.freshness?.measurements_stale;
  const problems = data.errors.map((error) => `<p class="notice warning">${esc(error.section)}: ${esc(error.message)}${error.retained ? " Showing values from the previous successful read." : ""}</p>`).join("");
  return `${problems}<section class="card"><div class="card-head"><h2>${esc(s.name)}</h2><span class="badge info">Synthetic AWS data</span></div>
    <p>Read from the shared exhibition service. Observations and device receipts in this fixture are simulated.</p>
    ${facts([["Showcase", s.showcase_id], ["Device", s.device_id], ["Confirmed exhibit", s.confirmed_exhibit_id],
      ["Applied profile version", s.confirmed_profile_version], ["Confirmed lighting mode", s.lighting_mode],
      ["Measurement freshness", stale === true ? "Stale" : stale === false ? "Recent" : "Unknown"],
      ["Observation time", dateTime(status?.recorded_at)], ["Temperature", status?.temperature_c == null ? "Unknown" : status.temperature_c + " °C"],
      ["Relative brightness", status?.brightness_percent == null ? "Unknown" : status.brightness_percent + " / 100"],
      ["Occupied", status?.occupied], ["Observed light output", status?.lighting_output], ["Observed latch", status?.latch]])}
    ${stale ? '<p class="notice warning">These measurements are stale. Fresh Python observations appear on the next live check.</p>' : ""}
    <h3>Confirmed label</h3>${label ? facts([["Title", label.content?.title], ["Collection", label.content?.collection],
      ["Description", label.content?.description], ["Draft version", label.draft_version], ["Acknowledged", dateTime(label.acknowledged_at)]]) : '<p>No label delivery has been acknowledged.</p>'}
  </section>
  ${table("Exhibit profiles", ["Exhibit", "Name", "Tag", "Version", "Temperature limits", "Brightness limits"], data.profiles.map((p) =>
    [p.exhibit_id, p.name, p.tag_id, p.version, p.temperature_min_c + "–" + p.temperature_max_c + " °C", p.brightness_min_percent + "–" + p.brightness_max_percent + " / 100"]))}
  ${table("Saved label drafts", ["Exhibit", "Title", "Description", "Version"], data.drafts.map((d) => [d.exhibit_id, d.content?.title, d.content?.description, d.version]))}
  ${table("Latest sensor readings", ["Observed at", "Exhibit", "Temperature °C", "Brightness ADC", "Occupied", "Source"], data.readings.map((r) =>
    [dateTime(r.recorded_at), r.exhibit_id, r.temperature_c, r.brightness_raw, r.occupied, r.source]), data.readingsMore)}
  <section class="card"><h2>Historical activity report</h2><p>Seeded event: 3 November 2026, 09:00–10:00 HKT. Current sensor updates do not create historical proximity intervals.</p>
    ${summary ? facts([["Occupied seconds", summary.recorded_occupied_seconds], ["Proximity event starts", summary.proximity_event_count],
      ["Covered seconds", summary.coverage_seconds], ["Missing seconds", summary.missing_seconds], ["Warnings", summary.warning_count],
      ["Saved report", data.saved?.report_id]]) : '<p>The report is unavailable.</p>'}
    <p class="muted">Event starts are not unique visitors. Missing observations remain unknown.</p></section>
  ${table("Command history", ["Command", "Type", "Status", "Receipt source", "Created"], data.commands.map((c) =>
    [c.command_id, c.type, c.status, c.acknowledgement_source, dateTime(c.created_at)]), data.commandsMore)}
  ${table("Operation history", ["Type", "Status", "Command", "Time"], data.activity.map((a) => [a.type, a.status, a.command_id, dateTime(a.created_at)]), data.activityMore)}
  ${role === "Administrator" || role === "Operator" ? table("Tag scans", ["Type", "Tag", "Observed at", "Source"], data.scans.map((s) =>
    [s.scan_type, s.tag_id, dateTime(s.recorded_at), s.source]), data.scansMore) : ""}
  ${role === "Administrator" ? `<section class="card"><h2>Demo card holder</h2>${data.cardHolder ? facts([["Name", data.cardHolder.name],
    ["Directory ID", data.cardHolder.staff_id], ["Card", data.cardHolder.card_id], ["Card enabled", data.cardHolder.card_enabled]]) : '<p>Demo card holder unavailable.</p>'}<p class="muted">This directory record has no website login.</p></section>` +
    table("Access history", ["Card", "Decision", "Observed at", "Source"], data.access.map((a) => [a.card_id, a.decision, dateTime(a.recorded_at), a.source]), data.accessMore) : ""}`;
}

export async function render(container, { document: pageDocument = globalThis.document, refreshRuntime = {} } = {}) {
  let disposed = false, sequence = 0, identity = "", controller = null, lastData = null;
  container.className = "stack live-demo";
  container.innerHTML = '<section class="card"><div class="card-head"><h2>Live demo showcase</h2><div class="actions"><label data-auto-control><input type="checkbox" data-auto checked> Automatic refresh</label><button class="button" data-refresh>Refresh live data</button></div></div><p>Sign in with a permitted staff account to inspect the seeded showcase and Python updates.</p><p data-auto-status class="muted" role="status"></p><div data-message class="notice info" role="status" aria-live="polite"></div><button class="button primary" data-sign-in>Staff sign in</button></section><div class="stack" data-showcase></div>';
  const message = container.querySelector("[data-message]");
  const content = container.querySelector("[data-showcase]");
  const refresh = container.querySelector("[data-refresh]");
  const signIn = container.querySelector("[data-sign-in]");
  const automatic = container.querySelector("[data-auto]");
  const automaticControl = container.querySelector("[data-auto-control]");
  const automaticStatus = container.querySelector("[data-auto-status]");
  automaticStatus.textContent = "Sign in to start live refresh.";
  function cancel() { sequence++; controller?.abort(); controller = null; }
  async function load() {
    cancel();
    const user = staffSession.snapshot().user;
    if (!user || disposed) return;
    const run = sequence;
    controller = new AbortController();
    message.className = "notice info";
    try {
      const data = await loadLiveShowcase((path, options) => staffSession.request(path, options), { role: user.role,
        signal: controller.signal, current: () => !disposed && run === sequence,
        onProgress: (section) => { message.textContent = "Loading " + section.toLowerCase() + "…"; } });
      if (disposed || run !== sequence) return;
      lastData = mergeLiveShowcase(lastData, data);
      content.innerHTML = showcaseHTML(lastData, user.role);
      message.textContent = data.missing ? "No live demo fixture found." : "Live read completed at " + dateTime(data.loadedAt) + ".";
    } catch (error) {
      if (disposed || run !== sequence || error.name === "AbortError") return;
      if (error.status === 401 || error.status === 403) { content.replaceChildren(); lastData = null; }
      message.className = "notice warning";
      message.textContent = (error.status === 403 ? "Your role cannot read this showcase." : error.message || "Live data could not be loaded. Try refreshing.") +
        (lastData ? " Previous data remains displayed from " + dateTime(lastData.loadedAt) + "." : "");
    }
  }
  const poller = createLiveRefresh({ ...refreshRuntime, load, cancel,
    changed({ active, enabled, busy }) {
      if (disposed) return;
      refresh.disabled = busy;
      automatic.checked = enabled;
      automaticStatus.textContent = !active ? pageDocument.hidden ? "Automatic refresh pauses while this tab is hidden." : "Sign in to start live refresh." :
        busy ? "Loading live data. Automatic refresh is " + (enabled ? "on." : "paused.") :
          enabled ? "Automatic refresh on · next check in 30 seconds." : "Automatic refresh paused. Use Refresh live data for a single check.";
    },
    onError(error) { if (!disposed) { message.className = "notice warning"; message.textContent = error.message || "Live refresh failed."; } },
  });
  function canRead(session) { return Boolean(session.user) && session.status !== "signing_out" && !pageDocument.hidden; }
  function sessionChanged(session) {
    const user = session.user;
    const next = user ? JSON.stringify([user.issuer, user.subject, user.role, user.account_version, session.status]) : "";
    if (next === identity && user) return;
    identity = next;
    poller.setActive(false);
    cancel();
    lastData = null;
    content.replaceChildren();
    signIn.hidden = Boolean(user);
    signIn.disabled = ["signing_in", "signing_out"].includes(session.status);
    refresh.hidden = !user;
    automaticControl.hidden = !user;
    if (canRead(session)) poller.setActive(true, true);
    else message.textContent = session.message || "Sign in to load live showcase records.";
  }
  function visibilityChanged() { poller.setActive(canRead(staffSession.snapshot())); }
  refresh.onclick = () => poller.run();
  automatic.onchange = () => poller.setEnabled(automatic.checked);
  signIn.onclick = async () => {
    try { await staffSession.startSignIn("#live-demo"); }
    catch (error) { if (!disposed) message.textContent = error.message; }
  };
  pageDocument.addEventListener("visibilitychange", visibilityChanged);
  const unsubscribe = staffSession.subscribe(sessionChanged);
  sessionChanged(staffSession.snapshot());
  return () => { disposed = true; poller.dispose(); unsubscribe(); pageDocument.removeEventListener("visibilitychange", visibilityChanged); container.replaceChildren(); };
}
