import { staffSession } from "./auth-session.js";
import { esc, reportError } from "./ui.js";

const ROLES = ["Administrator", "Operator", "Viewer"];
const id = (prefix) => prefix + "-" + crypto.randomUUID();
const errorText = (error) => error?.message || "The request could not be completed.";

async function startLogin() {
  try { await staffSession.startSignIn("#staff"); }
  catch (_error) { /* Session state supplies the user-facing error. */ }
}
async function endSession() {
  const result = await staffSession.signOut();
  if (result.logoutUrl && result.applicationRevoked) location.assign(result.logoutUrl);
  return result;
}

export async function initializeStaffLogin() {
  const button = document.querySelector("#staff-sign-in");
  const identity = document.querySelector(".staff-identity");
  function update(session) {
    button.textContent = session.status === "signing_in" ? "Signing in…" : session.status === "signing_out" ? "Signing out…" : session.user ? "Staff account" : "Staff sign in";
    button.disabled = ["signing_in", "signing_out"].includes(session.status);
    identity.innerHTML = session.user
      ? `<span class="avatar">${esc(session.user.name.slice(0, 2).toUpperCase())}</span><span>${esc(session.user.name)}<small>${esc(session.user.role)} · Live website account</small></span>`
      : '<span class="avatar">DE</span><span>Local demo<small>No live staff session</small></span>';
  }
  staffSession.subscribe(update);
  update(staffSession.snapshot());
  button.onclick = () => { location.hash = "#staff"; if (!staffSession.snapshot().user) startLogin().catch(reportError); };
  if (location.pathname === "/auth/callback.html") document.querySelector("#page-content").innerHTML = '<div class="card empty" role="status">Completing secure staff sign-in…</div>';
  try { await staffSession.completeCallback(); }
  catch (_error) { /* The Staff tab explains cancelled/invalid/expired callbacks. */ }
}

export async function renderLiveAccounts(container) {
  let disposed = false, sequence = 0, identity = "", staff = [], accounts = [], requestPlan = null;
  container.className = "card live-accounts";
  container.innerHTML = `<div class="card-head"><h2>Live website accounts</h2><span class="badge success">AWS account service</span></div>
    <p>Staff sign-in and website permissions use the live backend. Open <a href="#live-demo">Live showcase</a> for AWS test data. The dashboard uses the selected Demo or Live workspace. Website accounts and physical access cards are managed separately.</p>
    <div data-live-message class="notice info" role="status" aria-live="polite"></div>
    <div data-live-actions class="actions"></div><div data-live-account-content></div>`;
  const message = container.querySelector("[data-live-message]");
  const actions = container.querySelector("[data-live-actions]");
  const content = container.querySelector("[data-live-account-content]");
  const notice = (text, warning = false) => {
    if (disposed) return;
    message.textContent = text;
    message.className = "notice " + (warning ? "warning" : "info");
  };
  async function run(button, callback) {
    button.disabled = true;
    try { await callback(); }
    catch (error) { notice(errorText(error), true); }
    finally { if (!disposed && button.isConnected) button.disabled = false; }
  }
  async function pages(path) {
    const result = [], seen = new Set();
    let cursor = null;
    do {
      const query = new URLSearchParams({ limit: "100" });
      if (cursor) query.set("cursor", cursor);
      const page = await staffSession.request(path + "?" + query.toString());
      if (!Array.isArray(page.items) || !(page.next_cursor === null || typeof page.next_cursor === "string")) throw new Error("The service returned an invalid page.");
      result.push(...page.items);
      cursor = page.next_cursor;
      if (cursor && (seen.has(cursor) || seen.size >= 100)) throw new Error("Account pagination did not finish. Narrow the data set before retrying.");
      if (cursor) seen.add(cursor);
    } while (cursor);
    return result;
  }
  async function loadAdmin() {
    const current = ++sequence;
    content.innerHTML = '<p role="status">Loading live staff and account records…</p>';
    try {
      const values = await Promise.all([pages("/staff"), pages("/accounts")]);
      if (disposed || current !== sequence || !staffSession.snapshot().user?.permissions.manage_accounts) return;
      [staff, accounts] = values;
      drawAdmin();
      notice("Live account state loaded. Website permissions and physical cards are independent.");
    } catch (error) {
      if (disposed || current !== sequence) return;
      staff = accounts = [];
      content.replaceChildren();
      notice(errorText(error) + " Refresh permissions or reload account records to continue.", true);
    }
  }
  function drawAdmin() {
    const byStaff = new Map(accounts.map((account) => [account.staff_id, account]));
    content.innerHTML = `<div class="live-account-grid">
      <section><h3>Staff website access</h3><div class="table-wrap"><table><thead><tr><th>Staff</th><th>Website account</th><th>Role</th><th>Action</th></tr></thead><tbody>${staff.length ? staff.map((person) => {
        const account = byStaff.get(person.staff_id);
        return `<tr><td><strong>${esc(person.name)}</strong><small>${esc(person.email)}</small></td><td>${esc(account?.status || "Not invited")}</td><td>${esc(account?.role || "—")}</td><td><button class="button small" data-account="${esc(person.staff_id)}">${account ? "Manage" : "Invite"}</button></td></tr>`;
      }).join("") : '<tr><td colspan="4">No live staff records yet.</td></tr>'}</tbody></table></div><p class="muted">Only invited website accounts can sign in. A staff directory record alone grants no login permission.</p></section>
      <section data-account-editor><h3>Create staff and invite</h3><p class="muted">This action creates a live staff record and sends a real email invitation. No password is entered here.</p>
      <form data-live-invite><label>Name<input name="name" maxlength="100" required autocomplete="off"></label>
      <label>Email<input name="email" type="email" maxlength="160" required autocomplete="off"></label>
      <label>Website role<select name="role">${ROLES.map((role) => `<option ${role === "Viewer" ? "selected" : ""}>${role}</option>`).join("")}</select></label>
      <button class="button primary" type="submit">Create staff and send invitation</button></form></section></div>`;
    content.querySelectorAll("[data-account]").forEach((button) => {
      button.onclick = () => drawEditor(staff.find((person) => person.staff_id === button.dataset.account), byStaff.get(button.dataset.account));
    });
    bindInvitation(content.querySelector("[data-live-invite]"));
  }
  function bindInvitation(form, person = null) {
    requestPlan = null;
    form.onsubmit = (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type=submit]");
      run(button, async () => {
        if (!requestPlan) {
          const data = new FormData(form);
          const role = data.get("role");
          const staffBody = person || { staff_id: id("staff-web"), name: data.get("name").trim(), email: data.get("email").trim(), role };
          requestPlan = { staff: staffBody, createStaff: !person, invitation: { invitation_id: id("invite"), staff_id: staffBody.staff_id, role, delivery: "email" } };
          form.querySelectorAll("input,select").forEach((field) => { field.disabled = true; });
        }
        try {
          if (requestPlan.createStaff) await staffSession.request("/staff", { method: "POST", body: requestPlan.staff });
          await staffSession.request("/accounts/invitations", { method: "POST", body: requestPlan.invitation });
          requestPlan = null;
          await loadAdmin();
          notice("Invitation requested. Delivery and first sign-in are confirmed by the provider/account status; this action does not change a physical card.");
        } catch (error) {
          button.textContent = "Retry the same invitation request";
          throw new Error(errorText(error) + " The original staff/invitation IDs are retained for this form. Refresh records before retrying if the result was ambiguous.");
        }
      }).catch(reportError);
    };
  }
  function drawEditor(person, account) {
    requestPlan = null;
    const editor = content.querySelector("[data-account-editor]");
    if (!account) {
      editor.innerHTML = `<h3>Invite ${esc(person.name)}</h3><p>${esc(person.email)}</p><p class="muted">Send a live website invitation. This does not register or enable a physical card.</p><form data-live-invite><label>Website role<select name="role">${ROLES.map((role) => `<option ${role === "Viewer" ? "selected" : ""}>${role}</option>`).join("")}</select></label><button class="button primary" type="submit">Send invitation email</button></form>`;
      bindInvitation(editor.querySelector("form"), person);
      return;
    }
    editor.innerHTML = `<h3>Manage ${esc(person.name)}</h3><p>${esc(person.email)}</p><p>Website status: <strong>${esc(account.status)}</strong> · revision ${esc(account.version)}</p>
      <form data-live-update><label>Website role<select name="role">${ROLES.map((role) => `<option ${role === account.role ? "selected" : ""}>${role}</option>`).join("")}</select></label>
      <button class="button" type="submit">Save website role</button></form><div class="actions live-account-secondary">
      ${account.subject ? `<button class="button ${account.status === "disabled" ? "" : "danger"}" data-toggle>${account.status === "disabled" ? "Restore website access" : "Disable website access"}</button>` : ""}
      ${account.status === "invited" ? '<button class="button" data-resend>Resend invitation email</button>' : ""}
      ${account.status === "provisioning" ? '<button class="button" data-resume>Resume original enrollment</button>' : ""}</div>
      <p class="muted">Website roles apply to current API requests. Disabling a website account leaves its physical card unchanged. Last active Administrator protection is enforced by the backend.</p>`;
    async function update(body) {
      try {
        await staffSession.request("/accounts/" + encodeURIComponent(person.staff_id), { method: "PATCH", body, version: account.version });
        await staffSession.readSession();
        if (staffSession.snapshot().user?.permissions.manage_accounts) await loadAdmin();
        notice("Website account updated. Physical card permission was not changed.");
      } catch (error) {
        if (error.status === 409) {
          await loadAdmin();
          throw new Error("The account changed or the operation was blocked. Current records were reloaded; review them before another change. " + errorText(error));
        }
        throw error;
      }
    }
    editor.querySelector("form").onsubmit = (event) => {
      event.preventDefault();
      const button = event.currentTarget.querySelector("button");
      run(button, () => update({ role: new FormData(event.currentTarget).get("role") })).catch(reportError);
    };
    const toggle = editor.querySelector("[data-toggle]");
    if (toggle) toggle.onclick = () => run(toggle, () => update({ status: account.status === "disabled" ? "active" : "disabled" }));
    const resend = editor.querySelector("[data-resend]");
    let resendId = null;
    if (resend) resend.onclick = () => run(resend, async () => {
      resendId ||= id("resend");
      await staffSession.request("/accounts/" + encodeURIComponent(person.staff_id) + "/resend-invitation", { method: "POST", body: { request_id: resendId } });
      await loadAdmin();
      notice("Invitation resend requested. A timeout is not proof that email delivery failed.");
    });
    const resume = editor.querySelector("[data-resume]");
    if (resume) resume.onclick = () => run(resume, async () => {
      await staffSession.request("/accounts/invitations", { method: "POST", body: {
        invitation_id: account.invitation_id, staff_id: person.staff_id, role: account.role, delivery: account.delivery } });
      await loadAdmin();
      notice("Original enrollment resumed; the identity is not duplicated.");
    });
  }
  function drawSession(session) {
    if (disposed) return;
    const key = session.user ? session.user.issuer + "|" + session.user.subject + "|" + session.user.role : "";
    if (identity !== key || !session.user) {
      identity = key;
      sequence += 1;
      staff = accounts = [];
      requestPlan = null;
      content.replaceChildren();
    }
    notice(session.message, ["network_error", "login_error", "session_expired", "disabled_account"].includes(session.status));
    const busy = ["signing_in", "signing_out"].includes(session.status);
    actions.innerHTML = session.user
      ? `<span>${esc(session.user.name)} · <strong>${esc(session.user.role)}</strong></span><button class="button" data-check ${busy ? "disabled" : ""}>Refresh permissions</button><button class="button" data-out ${busy ? "disabled" : ""}>Sign out</button>`
      : `<button class="button primary" data-in ${busy ? "disabled" : ""}>Staff sign in</button>${session.status === "network_error" ? '<button class="button" data-check>Retry session check</button>' : ""}<span class="muted">Passwords, initial setup and recovery are handled on the provider sign-in page.</span>`;
    actions.querySelector("[data-in]")?.addEventListener("click", startLogin);
    actions.querySelector("[data-check]")?.addEventListener("click", (event) => run(event.currentTarget, () => staffSession.readSession()));
    actions.querySelector("[data-out]")?.addEventListener("click", (event) => run(event.currentTarget, async () => {
      const result = await endSession();
      if (result.logoutUrl && !result.applicationRevoked) {
        const finish = document.createElement("a");
        finish.href = result.logoutUrl;
        finish.className = "button";
        finish.textContent = "End provider browser session";
        actions.append(finish);
      }
    }));
    if (!session.user) return;
    if (!session.user.permissions.manage_accounts) {
      content.innerHTML = '<p>Your live session can access the data permitted by your website role. Staff/account administration requires an Administrator. Live dashboard reads follow the same signed-in account permissions.</p>';
    } else if (!content.childElementCount) {
      const reload = document.createElement("button");
      reload.className = "button";
      reload.textContent = "Load live accounts";
      reload.onclick = () => run(reload, loadAdmin);
      content.append(reload);
      loadAdmin().catch(reportError);
    }
  }
  const unsubscribe = staffSession.subscribe(drawSession);
  drawSession(staffSession.snapshot());
  return () => { disposed = true; sequence += 1; staff = accounts = []; requestPlan = null; unsubscribe(); container.replaceChildren(); };
}
