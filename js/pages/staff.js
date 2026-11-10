import {
  esc,
  icon,
  formatTime,
  formatDate,
  badge,
  toast,
  reportError,
  confirmAction,
  showDialog,
} from "../ui.js";

export async function render(container, ctx) {
  const { api } = ctx;
  const live = api.getMode?.() === "live";
  if (!api.can("staff")) {
    container.innerHTML = '<div class="notice info">Staff access is available to Administrators.</div>';
    return () => {};
  }
  let [snapshot, access, accounts, session] = await Promise.all([
    api.getSnapshot({ include: ["operations", "staff"] }),
    api.getAccessLog(),
    live && api.listAccounts ? api.listAccounts() : Promise.resolve([]),
    api.getSession(),
  ]);
  let staff = snapshot.staff;
  let disposed = false,
    refreshSequence = 0,
    scanning = false,
    registering = false,
    detected = null;
  let search = "",
    filter = "",
    resultFilter = "";
  let activeProfile = null;
  const cardOperations = () =>
    (snapshot.operations || []).filter((op) => op.type === "card");
  const pendingFor = (id) =>
    cardOperations().find(
      (op) => op.targetId === id && ["pending", "recovery_pending"].includes(op.status),
    );
  const initials = (name) =>
    String(name || "Staff")
      .trim()
      .split(/\s+/)
      .map((n) => n[0])
      .slice(0, 2)
      .join("")
      .toUpperCase();
  container.innerHTML = `<div class="staff-page stack">
    <div class="staff-status"><span class="staff-connection"></span><span>${live ? esc(snapshot.showcase.name || "Selected showcase") : "Case 01"}</span><span class="muted">Staff cards and exhibit tags are separate</span></div>
    <div class="notice info"><div>${live ? "<strong>Website accounts and physical cards</strong><p>Staff directory roles describe staff duties. Website permissions are managed separately below. Invitations are prepared without sending email; physical card access remains separate.</p>" : "<strong>Demo website login</strong><p>Every staff email uses the public shared demo password <code>DemoOnly!3070</code>. Adding staff enables their demo login; editing an email changes the login address. Physical access cards are separate.</p>"}</div></div>
    <div class="staff-columns">
      <section class="card staff-directory"><div class="card-head"><h2>${icon("users")} Staff directory</h2><div class="actions"><span class="muted staff-count"></span><button class="button primary small" data-add>${icon("plus")} Add staff</button></div></div>
        <div class="staff-filters"><label class="staff-search">${icon("search")}<input type="search" aria-label="Search staff or card" placeholder="Search staff or card…"></label><select aria-label="Filter staff"><option value="">All staff</option><option>Administrator</option><option>Operator</option><option>Viewer</option><option value="enabled">Card enabled</option><option value="revoked">Card revoked</option><option value="unassigned">No card assigned</option></select></div>
        <div class="table-wrap"><table class="staff-table"><thead><tr><th>Staff</th><th>${live ? "Directory role" : "Website role"}</th><th>Access card</th><th>Card access</th><th>Action</th></tr></thead><tbody data-directory></tbody></table></div>
        <p class="staff-footnote">${icon("info")} Card changes take effect after ${live ? "a device" : "a simulated showcase"} acknowledgement.</p>
        <div class="staff-roles"><h3>${live ? "Directory roles" : "Website roles"}</h3><dl><div><dt>Administrator</dt><dd>Manage staff, cards and dashboard controls</dd></div><div><dt>Operator</dt><dd>Manage exhibits, labels and lighting</dd></div><div><dt>Viewer</dt><dd>View readings, activity and reports</dd></div></dl><p class="muted">${live ? "Directory roles do not grant website account permissions. Manage the separate website accounts below. Physical card access is managed separately." : "Demo website controls follow these roles. This browser-only demo is not secure authentication. Physical card access is managed separately."}</p></div>
      </section>
      <section class="card staff-register"><div class="card-head"><h2>${icon("card")} Register access card</h2></div>
        <label class="staff-register-field"><span>Assign to staff</span><select data-assignee aria-label="Assign card to staff"></select></label>
        <label class="staff-register-field"><span>Showcase</span><select aria-label="Showcase"><option>${live ? esc(snapshot.showcase.name || "Selected showcase") : "Case 01"}</option></select></label>
        <div class="staff-scan"><div class="staff-scan-art" aria-hidden="true">${icon("scan")}${icon("card")}</div><h3 data-scan-title>Ready to scan</h3><p class="muted" data-scan-detail>No new card detected</p><div class="staff-detected" data-detected hidden></div><button class="button" data-scan>${icon("scan")} Start simulated scan</button><button class="button primary" data-register disabled>Confirm registration</button><p class="muted staff-scan-help">Scan a staff card, check its ID, then confirm.</p></div>
        <div class="notice info">${icon("info")} ${live ? "Waits for a new hardware staff-card scan with the matching scan attempt. Commands wait for real device acknowledgement." : "Mock scans and acknowledgements only."} Exhibit tags cannot grant access.</div><div data-card-status aria-live="polite"></div>
      </section>
    </div>
    <section class="card staff-access-log"><div class="card-head"><h2>${icon("report")} Access log</h2><select aria-label="Filter access log"><option value="">All results</option><option>Accepted</option><option>Denied</option></select></div><div class="table-wrap"><table><thead><tr><th>Time (HKT)</th><th>Card / staff</th><th>Result</th><th>Details</th></tr></thead><tbody data-log></tbody></table></div><p class="muted staff-log-note">${live ? "Recorded access attempts." : "Sample historical access records."} This dashboard has no remote unlock control.</p></section>
  </div>`;
  if (live) container.querySelector(".staff-page").insertAdjacentHTML("beforeend", `<section class="card staff-accounts"><div class="card-head"><h2>Website accounts</h2></div><p class="muted">Account permissions are independent of directory roles and physical cards.</p><div data-accounts></div></section>`);
  const $ = (selector) => container.querySelector(selector);
  const assignee = $("[data-assignee]"),
    scanButton = $("[data-scan]"),
    registerButton = $("[data-register]");
  function message(error) {
    return (
      error?.status === 409 || error?.code === "version_conflict" ? "This record changed on the server. Your edits are preserved. Copy your changes and reopen this form to load the current version." : error?.message ||
      "The operation could not be completed. Please try again."
    );
  }
  function drawDirectory() {
    $(".staff-count").textContent = `${staff.length} staff`;
    const visible = staff.filter((person) => {
      const matchesText = [
        person.name,
        person.email,
        person.cardId,
        person.role,
      ]
        .join(" ")
        .toLowerCase()
        .includes(search);
      const matchesFilter =
        !filter ||
        person.role === filter ||
        (filter === "enabled" && person.cardId && person.cardEnabled) ||
        (filter === "revoked" && person.cardId && !person.cardEnabled) ||
        (filter === "unassigned" && !person.cardId);
      return matchesText && matchesFilter;
    });
    $("[data-directory]").innerHTML = visible.length
      ? visible
          .map(
            (person) =>
              `<tr><td><div class="staff-person"><span class="staff-avatar">${esc(initials(person.name))}</span><span><strong>${esc(person.name)}</strong><small>${esc(person.email || "")}</small></span></div></td><td>${esc(person.role)}</td><td class="staff-card-id">${esc(person.cardId || "Not assigned")}</td><td>${pendingFor(person.id) ? badge("Pending confirmation", "warning") : person.cardId ? badge(person.cardEnabled ? "Enabled" : "Revoked", person.cardEnabled ? "success" : "neutral") : badge("No card", "neutral")}</td><td><button class="button small" data-manage="${esc(person.id)}">Manage</button></td></tr>`,
          )
          .join("")
      : '<tr><td colspan="5" class="empty">No staff match your filters.</td></tr>';
    const selected = assignee.value;
    assignee.innerHTML = staff.length
      ? '<option value="">Select staff…</option>' +
        staff
          .map(
            (person) =>
              `<option value="${esc(person.id)}">${esc(person.name)}</option>`,
          )
          .join("")
      : '<option value="">No staff available</option>';
    if (staff.some((person) => person.id === selected))
      assignee.value = selected;
    const latest = cardOperations()
      .slice()
      .sort((a, b) => b.requestedAt.localeCompare(a.requestedAt))
      .slice(0, 3);
    $("[data-card-status]").innerHTML = latest
      .map(
        (op) =>
          `<div class="staff-operation">${badge(op.status === "acknowledged" ? "Acknowledged" : op.status === "failed" ? "Failed" : op.status === "recovery_pending" ? "Recovery pending" : op.status === "recovered" ? "Recovered" : "Pending", op.status === "acknowledged" ? "success" : op.status === "failed" ? "danger" : op.status === "recovered" ? "info" : "warning")}<span>${esc(staff.find((person) => person.id === op.targetId)?.name || "Card change")} · ${esc(formatTime(op.requestedAt))}${op.error ? `<br>${esc(op.error)}` : ""}${op.status === "recovery_pending" ? "<br>Command locked until device recovery confirmation." : live && op.status === "pending" && op.executeBefore && Date.parse(op.executeBefore) <= Date.now() ? "<br>Execution deadline passed; review recovery in Activity. Command remains locked." : ""}</span></div>`,
      )
      .join("");
    $(".staff-connection").innerHTML = badge(
      snapshot.showcase.online ? (live ? "Online" : "Online · demo") : (live ? "Offline" : "Offline · demo"),
      snapshot.showcase.online ? "success" : "warning",
    );
    drawAccounts();
    updateScan();
  }
  function drawAccounts() {
    if (!live) return;
    $("[data-accounts]").innerHTML = staff.length ? `<div class="table-wrap"><table><thead><tr><th>Staff</th><th>Account role</th><th>Status</th><th>Action</th></tr></thead><tbody>${staff.map(person => {
      const account = accounts.find(item => (item.staffId || item.id) === person.id);
      return `<tr><td>${esc(person.name)}</td><td>${esc(account?.role || "No account")}</td><td>${esc(account?.status || "Not provisioned")}</td><td><button class="button small" data-account="${esc(person.id)}">${account ? "Manage account" : "Prepare invitation"}</button></td></tr>`;
    }).join("")}</tbody></table></div>` : '<div class="empty">Add a directory profile before preparing a website account.</div>';
    $("[data-accounts]").querySelectorAll("[data-account]").forEach(button => button.addEventListener("click", () => openAccount(staff.find(person => person.id === button.dataset.account))));
  }
  function openAccount(person) {
    const account = accounts.find(item => (item.staffId || item.id) === person.id);
    const own = person.id === session?.id || person.id === session?.staffId;
    showDialog(account ? "Manage website account" : "Prepare website invitation", `<form class="staff-account-form"><p>${esc(person.name)} · ${esc(person.email)}</p><p class="notice info">${account ? "Website permissions affect sign-in and dashboard controls. Physical cards and directory roles are separate." : "Prepare account provisioning with invitation email suppressed. This action does not send an invitation email."}</p><label class="field">Account role<select name="role" ${own ? "disabled" : ""}>${["Administrator","Operator","Viewer"].map(role => `<option ${role === (account?.role || "Viewer") ? "selected" : ""}>${role}</option>`).join("")}</select></label>${account ? `<label class="field">Account access<select name="status" ${own ? "disabled" : ""}><option value="unchanged">Keep current (${esc(account.status)})</option><option value="active">Active</option><option value="disabled">Disabled</option></select></label>` : ""}<p role="alert" class="staff-form-error"></p><div class="actions"><button type="button" class="button" data-cancel>Cancel</button><button type="submit" class="button primary" ${own ? "disabled" : ""}>${account ? "Save account" : "Prepare without email"}</button></div></form>`, dialog => {
      const form = dialog.querySelector("form"), close = dialog.close.bind(dialog);
      const original = JSON.stringify([...new FormData(form)]);
      let changed = false, saving = false, asking = false;
      form.addEventListener("change", () => { changed = JSON.stringify([...new FormData(form)]) !== original; ctx.setDirty(changed); });
      dialog.close = async () => { if (saving || asking) return; asking = true; const discard = !changed || await confirmAction("Discard account changes?", "Your unsaved account changes will be lost.", "Discard changes"); asking = false; if (discard) close(); };
      dialog.addEventListener("cancel", event => { event.preventDefault(); dialog.close(); });
      dialog.addEventListener("close", () => { ctx.setDirty(false); if (activeProfile?.dialog === dialog) activeProfile = null; }, { once:true });
      activeProfile = { dialog, forceClose:close };
      form.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());
      form.addEventListener("submit", async event => {
        event.preventDefault(); if (saving || own) return;
        const values = new FormData(form), role = values.get("role"), status = values.get("status");
        if (!(await confirmAction(account ? "Change website permissions?" : "Prepare account without email?", `${person.name} · ${role}${status && status !== "unchanged" ? ` · ${status}` : ""}. ${account ? "This affects website access." : "No invitation email will be sent."}`, account ? "Save account" : "Prepare account"))) return;
        saving = true; form.querySelector('[type="submit"]').disabled = true;
        try {
          if (account) await api.updateAccount(account.id || person.id, { role, ...(status === "unchanged" ? {} : { status }), version:account.version });
          else await api.prepareAccountInvitation(person.id, role);
          close(); toast(account ? "Website account updated." : "Account prepared without sending email."); await reload();
        } catch (error) { form.querySelector('[role="alert"]').textContent = message(error); }
        finally { saving = false; if (form.isConnected) form.querySelector('[type="submit"]').disabled = false; }
      });
    });
  }
  function drawLog() {
    const rows = access
      .filter((record) => !resultFilter || record.result === resultFilter)
      .slice()
      .sort((a, b) => b.at.localeCompare(a.at));
    $("[data-log]").innerHTML = rows.length
      ? rows
          .map(
            (record) =>
              `<tr><td><span>${esc(formatTime(record.at))}</span><small class="staff-log-date">${esc(formatDate(record.at))}</small></td><td>${esc(record.cardId || "Unregistered card")}${record.staffId ? ` · ${esc(staff.find((person) => person.id === record.staffId)?.name || "Unknown staff")}` : ""}</td><td>${badge(record.result, record.result === "Accepted" ? "success" : "danger")}</td><td>${esc(record.details)}</td></tr>`,
          )
          .join("")
      : '<tr><td colspan="4" class="empty">No access records match this filter.</td></tr>';
  }
  function updateScan() {
    const person = staff.find((row) => row.id === assignee.value);
    registerButton.disabled =
      !detected ||
      !person ||
      scanning ||
      registering ||
      !!pendingFor(person.id);
    registerButton.textContent = registering
      ? "Requesting registration…"
      : "Confirm registration";
    scanButton.disabled = scanning || registering;
    scanButton.innerHTML = `${icon("scan")} ${scanning ? "Scanning…" : detected ? "Scan another card" : live ? "Start hardware scan wait" : "Start simulated scan"}`;
    $("[data-detected]").hidden = !detected;
    $("[data-detected]").textContent = detected || "";
  }
  async function reload() {
    const sequence = ++refreshSequence;
    try {
      const values = await Promise.all([
        api.getSnapshot({ include: ["operations", "staff"] }),
        api.getAccessLog(),
        live && api.listAccounts ? api.listAccounts() : Promise.resolve([]),
      ]);
      if (disposed || sequence !== refreshSequence) return;
      [snapshot, access, accounts] = values;
      staff = snapshot.staff;
      drawDirectory();
      drawLog();
    } catch (error) {
      if (!disposed) toast(message(error), "danger");
    }
  }
  function openProfile(person = null) {
    if (!api.can("staff")) return;
    const ownProfile = person?.id === session?.id;
    showDialog(
      person ? "Manage staff" : "Add staff",
      `<form class="staff-profile-form"><div class="form-grid"><label class="field">Full name<input name="name" value="${esc(person?.name || "")}" maxlength="80" required></label><label class="field">Email<input type="email" name="email" value="${esc(person?.email || "")}" maxlength="150" required></label><label class="field">${live ? "Directory role" : "Website role"}<select name="role" ${ownProfile ? "disabled" : ""}>${["Administrator", "Operator", "Viewer"].map((role) => `<option${role === (person?.role || "Viewer") ? " selected" : ""}>${role}</option>`).join("")}</select></label></div><p class="notice info">${ownProfile ? "Your own website role cannot be changed here. " : ""}${live ? "This changes directory metadata only. Website permissions are managed in Website accounts; physical card permissions remain separate." : "Website controls follow the selected demo role. All staff use the public shared password DemoOnly!3070; changing this email changes the login address. Secure authentication and authorization require a backend."}</p><p class="staff-form-error" role="alert"></p><div class="actions"><button class="button" type="button" data-cancel>Cancel</button><button class="button primary" type="submit">${person ? "Save profile" : "Add staff"}</button></div></form>${person ? `<div class="staff-manage-card"><h3>Physical access card</h3><p>${esc(person.cardId || "No card assigned")} · ${esc(person.cardId ? (person.cardEnabled ? "Enabled" : "Revoked") : "Use the registration panel to assign a card.")}</p>${person.cardId ? `<button class="button ${person.cardEnabled ? "danger" : ""}" data-access ${pendingFor(person.id) ? "disabled" : ""}>${person.cardEnabled ? "Revoke card access" : "Restore card access"}</button>` : ""}${pendingFor(person.id) ? '<p class="muted">A card change is awaiting acknowledgement.</p>' : ""}</div>` : ""}`,
      (dialog) => {
        const form = dialog.querySelector("form");
        const originalClose = dialog.close.bind(dialog);
        const profileValues = () => JSON.stringify([...new FormData(form)]);
        const initialValues = profileValues();
        let profileDirty = false,
          confirmingClose = false,
          saving = false;
        const updateDirty = () => {
          profileDirty = profileValues() !== initialValues;
          ctx.setDirty(profileDirty);
        };
        form.addEventListener("input", updateDirty);
        form.addEventListener("input", (event) => {
          event.target.removeAttribute("aria-invalid");
          event.target.removeAttribute("aria-describedby");
          event.target
            .closest(".field")
            ?.querySelector(".field-error")
            ?.remove();
        });
        form.addEventListener("change", updateDirty);
        async function requestClose() {
          if (saving || confirmingClose) return false;
          confirmingClose = true;
          const discard =
            !profileDirty ||
            (await confirmAction(
              "Discard profile changes?",
              "Your unsaved staff profile changes will be lost.",
              "Discard changes",
            ));
          confirmingClose = false;
          if (!discard || disposed) return false;
          originalClose();
          return true;
        }
        // Shared dialog chrome and backdrop use close(); intercept all dismiss paths.
        dialog.close = requestClose;
        dialog.addEventListener("cancel", (event) => {
          event.preventDefault();
          requestClose().catch(reportError);
        });
        dialog.addEventListener(
          "close",
          () => {
            ctx.setDirty(false);
            if (activeProfile?.dialog === dialog) activeProfile = null;
          },
          { once: true },
        );
        activeProfile = { dialog, forceClose: originalClose };
        dialog
          .querySelector("[data-cancel]")
          .addEventListener("click", () => dialog.close());
        dialog
          .querySelector("form")
          .addEventListener("submit", async (event) => {
            event.preventDefault();
            const form = event.currentTarget,
              button = form.querySelector('[type="submit"]');
            const values = new FormData(form),
              name = String(values.get("name")).trim(),
              email = String(values.get("email")).trim();
            if (!name) {
              form.querySelector('[role="alert"]').textContent =
                "Enter a staff name.";
              return;
            }
            button.disabled = true;
            saving = true;
            try {
              await api.saveStaff({
                ...(person || {}),
                name,
                email,
                role: ownProfile ? person.role : values.get("role"),
              });
              originalClose();
              if (!disposed) {
                toast(live ? "Staff directory profile saved." : "Staff profile saved locally.");
                await reload();
              }
            } catch (error) {
              if (!disposed) {
                form.querySelector('[role="alert"]').textContent =
                  message(error);
                for (const [name, detail] of Object.entries(
                  error.fields || {},
                )) {
                  const input = form.elements[name];
                  if (!input) continue;
                  input
                    .closest(".field")
                    .querySelector(".field-error")
                    ?.remove();
                  const help = document.createElement("small");
                  help.className = "field-error";
                  help.id = `staff-error-${name}`;
                  help.textContent = detail;
                  input.closest(".field").append(help);
                  input.setAttribute("aria-invalid", "true");
                  input.setAttribute("aria-describedby", help.id);
                  input.focus();
                }
                button.disabled = false;
              }
            } finally {
              saving = false;
            }
          });
        dialog
          .querySelector("[data-access]")
          ?.addEventListener("click", async (event) => {
            const enabled = !person.cardEnabled;
            // Close this modal before opening a separate confirmation modal.
            if (!(await requestClose())) return;
            if (
              !(await confirmAction(
                enabled ? "Restore card access?" : "Revoke card access?",
                `${person.name} · ${person.cardId}. ${live ? "This change" : "This simulated change"} takes effect only after showcase acknowledgement.`,
                enabled ? "Restore access" : "Revoke access",
              ))
            )
              return;
            try {
              await api.setCardAccess(person.id, enabled, live ? { expectedCardId: person.cardId } : undefined);
              toast(
                live ? "Card access change requested; awaiting device acknowledgement." : "Card access change requested; awaiting mock acknowledgement.",
                "info",
              );
              await reload();
            } catch (error) {
              toast(message(error), "danger");
            }
          });
      },
    );
  }
  $(".staff-search input").addEventListener("input", (event) => {
    search = event.target.value.trim().toLowerCase();
    drawDirectory();
  });
  $(".staff-filters select").addEventListener("change", (event) => {
    filter = event.target.value;
    drawDirectory();
  });
  $(".staff-access-log select").addEventListener("change", (event) => {
    resultFilter = event.target.value;
    drawLog();
  });
  $("[data-add]").addEventListener("click", () => openProfile());
  $("[data-directory]").addEventListener("click", (event) => {
    const button = event.target.closest("[data-manage]");
    if (button)
      openProfile(staff.find((person) => person.id === button.dataset.manage));
  });
  assignee.addEventListener("change", updateScan);
  scanButton.addEventListener("click", async () => {
    scanning = true;
    detected = null;
    updateScan();
    $("[data-scan-title]").textContent = live ? "Checking hardware scan" : "Simulated scan in progress";
    $("[data-scan-detail]").textContent = live ? "Waiting for new staff-card evidence…" : "Waiting for a mock staff card…";
    try {
      const scan = await api.scanStaffCard(live ? { onWaiting: ({ scanAttemptId }) => { if (!disposed) $("[data-scan-detail]").textContent = `Waiting for hardware registration scan. Scan attempt: ${scanAttemptId}`; } } : undefined);
      if (disposed) return;
      if (!scan || !/^CARD-[A-Z0-9-]+$/i.test(scan.id || ""))
        throw new Error(
          "Invalid staff card ID detected. Please scan a staff access card.",
        );
      const duplicate = staff.find(
        (person) => person.cardEnabled && person.cardId === scan.id,
      );
      if (duplicate)
        throw new Error(
          `This card is already enabled for ${duplicate.name}. Revoke it before assigning it again.`,
        );
      detected = scan.id;
      $("[data-scan-title]").textContent = "Staff card detected";
      $("[data-scan-detail]").textContent =
        "Check the ID below and choose a staff member.";
    } catch (error) {
      if (!disposed) {
        $("[data-scan-title]").textContent = "Scan unsuccessful";
        $("[data-scan-detail]").textContent = message(error);
      }
    } finally {
      scanning = false;
      if (!disposed) updateScan();
    }
  });
  registerButton.addEventListener("click", async () => {
    const person = staff.find((row) => row.id === assignee.value);
    if (!person || !detected || pendingFor(person.id) || registering) return;
    const cardId = detected;
    if (
      !(await confirmAction(
        person.cardId ? "Replace assigned card?" : "Register access card?",
        `Register ${cardId} for ${person.name}? ${person.cardId ? `The previous card ${person.cardId} will be replaced. ` : ""}Access changes after ${live ? "device" : "mock"} acknowledgement.`,
        "Register card",
      ))
    )
      return;
    registering = true;
    updateScan();
    try {
      await api.registerCard(person.id, cardId);
      detected = null;
      $("[data-scan-title]").textContent = "Registration requested";
      $("[data-scan-detail]").textContent =
        live ? "Awaiting device acknowledgement." : "Awaiting simulated showcase acknowledgement.";
      toast(live ? "Registration pending device acknowledgement." : "Registration pending mock acknowledgement.", "info");
      await reload();
    } catch (error) {
      $("[data-scan-title]").textContent = "Registration unsuccessful";
      $("[data-scan-detail]").textContent = message(error);
    } finally {
      registering = false;
      if (!disposed) updateScan();
    }
  });
  drawDirectory();
  drawLog();
  const unsubscribe = api.subscribe(reload);
  return () => {
    disposed = true;
    refreshSequence++;
    unsubscribe();
    activeProfile?.forceClose();
    activeProfile = null;
    ctx.setDirty(false);
  };
}
