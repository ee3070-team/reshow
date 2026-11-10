import { esc, icon } from "./ui.js";

// Demo persona selection is deliberately separate from provider-hosted live login.
export function renderLogin(container, api, onSignedIn, message = "") {
  container.innerHTML = `<div class="login-layout">
    <div class="login-intro">
      <div class="brand"><img src="assets/logo.svg" alt=""><span>ReShow<small>Smart showcase</small></span></div>
      <div class="login-intro-copy"><span class="login-eyebrow">EXHIBITION WORKSPACE</span><h1>Your showcase.<br>One shared workspace.</h1><p>Keep exhibits, electronic labels and showcase activity in view.</p></div>
      <div class="login-role-list"><div>${icon("users")}<span><strong>Admin</strong>Manage staff, access cards and the dashboard.</span></div><div>${icon("cube")}<span><strong>Operator</strong>Manage exhibits, labels and lighting.</span></div><div>${icon("activity")}<span><strong>Viewer</strong>Explore readings, activity and reports.</span></div></div>
      <p class="login-intro-footer">ReShow prototype · School exhibition</p>
    </div>
    <div class="login-panel"><div class="login-card">
      <div class="login-mode-switch" role="group" aria-label="Workspace mode"><button type="button" data-mode="demo">Demo</button><button type="button" data-mode="live">Live</button></div>
      <span class="badge demo" id="login-mode-badge">Demo workspace</span><h2>Staff sign in</h2><p class="muted" id="login-mode-description">Choose a sample staff email to explore the local demo. No password is needed.</p>
      <div id="demo-login-area">
      <form id="login-form">
        <label class="field" for="login-email">Email address<input id="login-email" name="email" type="email" autocomplete="off" maxlength="150" placeholder="name@reshow.demo" required></label>
        <p class="field-error login-error" id="login-error" role="alert">${esc(message)}</p>
        <button class="button primary full" type="submit">Open demo ${icon("arrow")}</button>
      </form>
      <details class="demo-accounts"><summary>Demo accounts</summary><p>Choose a demo email, then select Open demo.</p><div class="demo-account-list">${[["Admin", "sarah.chen@reshow.demo"], ["Operator", "james.wong@reshow.demo"], ["Viewer", "emily.lau@reshow.demo"]].map(([role,email]) => `<button type="button" class="demo-account" data-demo-email="${email}"><strong>${role}</strong><span>${email}</span></button>`).join("")}</div><small>If an admin changes a staff email, use the updated address.</small></details>
      <p class="login-demo-note" id="demo-login-note">${icon("info")} Local simulation only. This demo does not authenticate you or grant live access. Choose Live to sign in with a real staff account.</p>
      </div><div id="live-login-area" hidden><p class="login-live-note">Continue to the secure staff login provider. Your identity and permissions are checked by the live service. Reloading this tab requires sign in again.</p><p class="field-error" id="live-login-error" role="alert"></p><button type="button" class="button primary full" id="live-login">Continue to live sign in ${icon("arrow")}</button><p class="login-demo-note">Live mode reads and changes the connected backend. Device changes remain pending until producer acknowledgement.</p></div>
    </div></div>
  </div>`;
  const form = container.querySelector("form");
  const email = form.elements.email;
  const error = container.querySelector("#login-error");
  const submit = form.querySelector('[type="submit"]');
  let busy = false, disposed = false;
  function updateMode() {
    const live = api.getMode() === "live";
    container.querySelector("#demo-login-area").hidden = live;
    container.querySelector("#live-login-area").hidden = !live;
    container.querySelector("#login-mode-badge").textContent = live ? "Live workspace" : "Demo workspace";
    container.querySelector("#login-mode-description").textContent = live ? "Sign in with your provisioned staff account." : "Choose a sample staff email to explore the local demo. No password is needed.";
    container.querySelectorAll("[data-mode]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.mode === api.getMode())));
    container.querySelector("#live-login-error").textContent = live ? message : "";
    error.textContent = live ? "" : message;
  }
  container.querySelectorAll("[data-mode]").forEach(button => {
    button.onclick = async () => {
      if (busy) return;
      message = "";
      await api.selectMode(button.dataset.mode);
      updateMode();
    };
  });
  container.querySelector("#live-login").onclick = async event => {
    if (busy) return;
    busy = true;
    event.currentTarget.disabled = true;
    const button = event.currentTarget;
    button.textContent = "Opening secure sign in…";
    try { await api.startLiveLogin(); }
    catch (issue) {
      if (!disposed) {
        container.querySelector("#live-login-error").textContent = issue.message || "Could not start live sign in.";
        button.disabled = false;
        button.textContent = "Continue to live sign in";
      }
      busy = false;
    }
  };
  updateMode();
  container.querySelectorAll("[data-demo-email]").forEach(button => {
    button.onclick = () => {
      if (busy) return;
      email.value = button.dataset.demoEmail;
      email.focus();
      error.textContent = "";
    };
  });
  form.onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    error.textContent = "";
    submit.disabled = true;
    email.readOnly = true;
    submit.textContent = "Opening demo…";
    form.setAttribute("aria-busy", "true");
    try {
      await api.signIn(email.value);
      if (!disposed) await onSignedIn();
    } catch (issue) {
      if (!disposed) {
        error.textContent = issue.message || "Could not open this demo account. Please try again.";
        email.focus();
      }
    } finally {
      busy = false;
      if (!disposed) {
        submit.disabled = false;
        email.readOnly = false;
        submit.innerHTML = `Open demo ${icon("arrow")}`;
        form.removeAttribute("aria-busy");
      }
    }
  };
  if (api.getMode() === "demo") email.focus();
  return () => { disposed = true; };
}
