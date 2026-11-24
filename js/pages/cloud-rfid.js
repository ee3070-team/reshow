export function bindCloudRfid(container, ctx, signal) {
  const showcase = container.querySelector("[data-cloud-showcase]");
  const state = container.querySelector("[data-cloud-rfid-state]");
  const delivery = container.querySelector("[data-cloud-rfid-delivery]");
  const form = container.querySelector("[data-cloud-rfid-form]");
  const message = form.querySelector("[data-cloud-rfid-message]");
  const description = form.querySelector("textarea");
  const send = form.querySelector('[type="submit"]');
  const cancel = form.querySelector("[data-cloud-rfid-cancel]");
  const canOperate = ctx.api.can("operate");
  if (!canOperate) {
    showcase.disabled = true;
    state.textContent = "An Operator or Administrator account is required.";
    return () => {};
  }

  let session;
  let busy = false;
  let dirty = false;
  let draftEditId;
  let pending = false;

  function enableControls() {
    const canEdit = !busy && Boolean(session?.edit_id);
    cancel.disabled = !canEdit;
    description.disabled = busy || (!dirty && (!canEdit || !session.exhibit_id));
    send.disabled = !canEdit || !session.exhibit_id || (dirty && draftEditId !== session.edit_id);
    showcase.disabled = dirty || busy || showcase.options.length === 0;
  }

  function showSession(next) {
    if (session?.edit_id !== next.edit_id && dirty) {
      message.textContent = "The edit session changed. Copy your unsaved text before clearing it.";
    }
    session = next;
    state.textContent = next.edit_id
      ? `Edit open for ${next.exhibit_id || "a showcase without an assigned exhibit"}.`
      : "Waiting for a door or e-paper card tap.";
    if (!next.exhibit_id) state.textContent += " Confirm an exhibit assignment in Exhibits before sending a label.";
    if (next.last_tap) {
      const { role, result, recorded_at: at } = next.last_tap;
      state.textContent += ` Last tap: ${role || "unknown card"} (${result}), ${new Date(at).toLocaleString()}.`;
    }
    const command = next.delivery;
    const expired = command?.status === "pending" && Date.parse(command.execute_before) <= Date.now();
    delivery.textContent = ({
      pending: expired ? "The delivery window expired. Reconcile the command in Operations."
        : "Waiting for the Mega to confirm the e-paper update.",
      acknowledged: "The Mega confirmed the e-paper update.",
      failed: `E-paper update failed. ${command?.failure_reason || ""}`.trim(),
      recovery_pending: "E-paper update recovery is pending.",
    })[command?.status] || (command ? `E-paper delivery: ${command.status}.` : "No RFID label has been queued.");
    enableControls();
  }

  async function refresh() {
    if (pending || busy || !showcase.value || signal.aborted) return;
    const id = showcase.value;
    pending = true;
    try {
      const next = await ctx.api.getRfidSession(id, { signal });
      if (!signal.aborted && id === showcase.value && !busy) showSession(next);
    } catch (error) {
      if (!signal.aborted && id === showcase.value && !busy) {
        session = null;
        state.textContent = error.message;
        delivery.textContent = "";
        enableControls();
      }
    } finally {
      pending = false;
    }
  }

  async function finish(publish) {
    if (busy || !session?.edit_id || (publish && send.disabled)) return;
    const text = description.value.trim();
    if (publish && !/^[\x20-\x7e]{1,180}$/.test(text)) {
      message.textContent = "Enter 1–180 printable ASCII characters.";
      return;
    }
    const label = publish ? { exhibit_id: session.exhibit_id, description: text } : null;
    busy = true;
    enableControls();
    try {
      const next = await ctx.api.finishRfidSession(showcase.value, session.edit_id, label, { signal });
      if (signal.aborted) return;
      description.value = "";
      dirty = false;
      ctx.setDirty(false);
      showSession(next);
      message.textContent = publish ? "Label queued for delivery." : "Edit finished without publishing.";
    } catch (error) {
      if (!signal.aborted) message.textContent = error.message;
    } finally {
      busy = false;
      if (!signal.aborted) {
        enableControls();
        void refresh();
      }
    }
  }

  description.addEventListener("input", () => {
    if (!dirty) draftEditId = session?.edit_id;
    dirty = Boolean(description.value);
    if (!dirty) message.textContent = "";
    ctx.setDirty(dirty);
    enableControls();
  }, { signal });
  showcase.addEventListener("change", () => {
    session = null;
    state.textContent = "Loading RFID session…";
    delivery.textContent = message.textContent = "";
    enableControls();
    void refresh();
  }, { signal });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void finish(true);
  }, { signal });
  cancel.addEventListener("click", () => void finish(false), { signal });

  ctx.api.getShowcases().then((items) => {
    if (signal.aborted) return;
    showcase.replaceChildren(...items.map(({ id, name }) => new Option(name, id)));
    if (!items.length) state.textContent = "No showcase is registered.";
    enableControls();
    void refresh();
  }).catch((error) => {
    if (!signal.aborted) state.textContent = error.message;
  });
  enableControls();
  const timer = window.setInterval(refresh, 3000);
  return () => window.clearInterval(timer);
}
