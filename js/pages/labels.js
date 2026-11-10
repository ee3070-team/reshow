import {
  esc,
  icon,
  formatTime,
  formatDate,
  badge,
  toast,
  confirmAction,
  fileToDataUrl,
  statusStrip,
} from "../ui.js";

const message = (error) =>
  error?.status === 409 || error?.code === "version_conflict" ? "This record changed on the server. Your edits are preserved. Copy your changes, then reopen the editor to load the current version before retrying." : error?.message || "The label could not be updated. Please try again.";
const same = (a, b) => {
  const normalized = value => value && typeof value === "object" ? Object.fromEntries(Object.keys(value).filter(key => key !== "version" && key !== "imageWarning" && !(key === "image" && Object.hasOwn(value, "imageMediaId"))).sort().map(key => [key, value[key]])) : value;
  return JSON.stringify(normalized(a)) === JSON.stringify(normalized(b));
};
const imageAttributes = (image) => (image ? `src="${esc(image)}"` : "hidden");

export async function render(container, ctx) {
  const live = ctx.api.getMode?.() === "live";
  const canOperate = ctx.api.can("operate");
  let snapshot = await ctx.api.getSnapshot({ include: ["exhibits", "operations"] });
  const exhibits = snapshot.exhibits;
  const caseName = () => live ? snapshot.showcase.name || "Selected showcase" : "Case 01";
  let selectedId =
    exhibits.find((item) => item.id === ctx.params?.exhibitId)?.id ||
    exhibits.find((item) => item.id === snapshot.showcase.exhibitId)?.id ||
    exhibits[0]?.id ||
    null;
  let label = selectedId ? await ctx.api.getLabel(selectedId) : null;
  let localPreview = "";
  let editVersion = label?.draft?.version ?? label?.draftVersion ?? 0;
  let content,
    baseline,
    disposed = false,
    revision = 0,
    busy = false;
  let stopSubscription = () => {};
  const defaultContent = () => {
    const exhibit = exhibits.find((item) => item.id === selectedId);
    return {
      title: exhibit?.name || "",
      collection: exhibit?.collection || (live ? "" : "Student collection"),
      description: exhibit?.description || "",
      image: exhibit?.image || "",
      ...(live ? { imageMediaId: exhibit?.imageMediaId ?? null } : {}),
    };
  };
  const initialContent = () =>
    label?.draft ||
    (label?.confirmed && label.confirmed.exhibitId === selectedId
      ? label.confirmed.content
      : null) ||
    defaultContent();
  content = { ...initialContent() };
  baseline = { ...content };
  const dirty = () => canOperate && !same(content, baseline);
  const exhibit = () => exhibits.find((item) => item.id === selectedId);
  const statusTone = (status) =>
    status === "acknowledged"
      ? "success"
      : status === "recovered"
        ? "info"
      : status === "failed"
        ? "danger"
        : "warning";

  function draw() {
    if (!selectedId || !exhibit()) {
      container.innerHTML = `<div data-page-device-status>${statusStrip(snapshot)}</div><section class="card empty">${icon("tag")}<h2>No exhibit available</h2><p>${canOperate ? "Add an exhibit profile before creating an electronic label." : "No exhibit profiles are available to view."}</p><button type="button" class="button primary" data-open-exhibits>Open exhibits</button></section>`;
      container
        .querySelector("[data-open-exhibits]")
        .addEventListener("click", () => ctx.navigate("exhibits"));
      return;
    }
    container.innerHTML = `<div data-page-device-status>${statusStrip(snapshot)}</div><div class="label-selector"><label for="label-exhibit">Linked exhibit</label><select id="label-exhibit">${exhibits.map((item) => `<option value="${esc(item.id)}" ${item.id === selectedId ? "selected" : ""}>${esc(item.name)} · ${esc(item.tagId)}</option>`).join("")}</select></div><div class="label-layout"><section class="card label-editor"><div class="card-head"><div class="heading-with-icon">${icon("tag")}<div><h2>Label content</h2><p class="muted">Linked exhibit: ${esc(exhibit().name)} · ${esc(exhibit().tagId)}</p></div></div></div><form><label class="field">Exhibit title<input name="title" maxlength="${live ? 100 : 90}" required value="${esc(content.title)}"></label><label class="field">Collection<input name="collection" maxlength="${live ? 120 : 100}" ${live ? "" : "required"} value="${esc(content.collection)}"></label><label class="field">Short description<textarea name="description" maxlength="${live ? 2000 : 400}" rows="3" required>${esc(content.description)}</textarea><small class="muted">${canOperate ? "Keep text short for the e-paper display." : "Saved content is shown for review."}</small></label><div class="label-image-control"><img data-editor-image ${imageAttributes(content.image)} alt="Current draft exhibit image"><label class="button image-upload">${icon("upload")} ${live ? "Local image preview" : "Replace image"}<input type="file" accept="image/png,image/jpeg,image/webp" data-image></label></div>${live ? '<p class="notice info">Preview only—not uploaded. Saving preserves the existing remote image reference.</p>' : ""}${content.imageWarning ? `<p class="notice warning">${esc(content.imageWarning)} The saved image reference is preserved.</p>` : ""}<div class="notice warning" data-form-error role="alert" hidden></div><div data-draft-status aria-live="polite"></div><div class="label-editor-actions"><button type="button" class="button" data-save>${icon("report")} Save draft</button><button type="submit" class="button primary" data-send>${icon("arrow")} Send to showcase</button></div><p class="muted small-text" data-send-help></p></form></section><section class="card label-preview-card"><div class="card-head"><h2 class="heading-with-icon">${icon("tag")} Label preview</h2>${badge("Draft", "info")}</div><div class="epaper-frame"><div class="epaper-screen"><img data-preview-image ${imageAttributes(content.image)} alt=""><div><h3 data-preview-title></h3><p data-preview-collection></p><hr><p data-preview-description></p></div></div></div><p class="muted small-text preview-caption">Illustrative preview · Final fit depends on display size</p><div class="device-label-section"><h2 class="heading-with-icon">${icon("cube")} Currently on the showcase</h2><div data-label-reconciliation aria-live="polite"></div><div data-device-label aria-live="polite"></div></div></section></div><section class="card label-history"><h2 class="heading-with-icon">${icon("activity")} Update history</h2><div data-label-history></div><p class="muted small-text">${live ? "An update" : "A mock update"} is confirmed only after the showcase acknowledges it.</p></section>`;
    const form = container.querySelector("form");
    if (!canOperate) {
      container.insertAdjacentHTML("afterbegin", '<div class="notice info">Viewer access · Read-only label content, preview and delivery history.</div>');
      form.querySelectorAll("input[name], textarea").forEach((field) => { field.readOnly = true; });
      form.querySelector("[data-image]").disabled = true;
      form.querySelector(".image-upload").hidden = true;
      form.querySelector(".label-editor-actions").hidden = true;
    }
    for (const name of ["title", "collection", "description"])
      form.elements[name].addEventListener("input", (event) => {
        if (!ctx.api.can("operate")) return;
        content[name] = event.target.value;
        ctx.setDirty(dirty());
        drawPreview();
        drawStatus();
      });
    container
      .querySelector("[data-image]")
      .addEventListener("change", async (event) => {
        if (!ctx.api.can("operate")) return;
        const file = event.target.files[0];
        if (!file) return;
        const forId = selectedId;
        try {
          if (
            !["image/png", "image/jpeg", "image/webp"].includes(file.type) ||
            file.size > 1024 * 1024
          )
            throw new Error(
              "Choose a PNG, JPG, or WebP image smaller than 1 MB.",
            );
          const image = await fileToDataUrl(file);
          if (disposed || forId !== selectedId) return;
          if (live) localPreview = image;
          else content.image = image;
          ctx.setDirty(dirty());
          drawPreview();
          drawStatus();
        } catch (error) {
          showError(error);
        }
        event.target.value = "";
      });
    container.querySelector("[data-save]").addEventListener("click", saveDraft);
    form.addEventListener("submit", sendLabel);
    container
      .querySelector("#label-exhibit")
      .addEventListener("change", async (event) => {
        const nextId = event.target.value;
        if (
          dirty() &&
          !(await confirmAction(
            "Discard unsaved label changes?",
            "The current edits have not been saved as a draft. Discard them and open the selected exhibit?",
            "Discard changes",
          ))
        ) {
          event.target.value = selectedId;
          return;
        }
        busy = true;
        event.target.disabled = true;
        try {
          const nextLabel = await ctx.api.getLabel(nextId);
          if (disposed) return;
          selectedId = nextId;
          label = nextLabel;
          editVersion = label?.draft?.version ?? label?.draftVersion ?? 0;
          localPreview = "";
          revision++;
          content = { ...initialContent() };
          baseline = { ...content };
          ctx.setDirty(false);
          busy = false;
          draw();
        } catch (error) {
          busy = false;
          event.target.disabled = false;
          event.target.value = selectedId;
          showError(error);
        }
      });
    drawPreview();
    drawStatus();
  }

  function showError(error) {
    const node = container.querySelector("[data-form-error]");
    if (node) {
      node.textContent = message(error);
      node.hidden = false;
    } else toast(message(error), "error");
  }
  function drawPreview() {
    container.querySelector("[data-preview-title]").textContent =
      content.title || "Exhibit title";
    container.querySelector("[data-preview-collection]").textContent =
      content.collection || "Collection";
    container.querySelector("[data-preview-description]").textContent =
      content.description || "Short exhibit description";
    for (const selector of ["[data-preview-image]", "[data-editor-image]"]) {
      const image = container.querySelector(selector);
      if (localPreview || content.image) image.setAttribute("src", localPreview || content.image);
      else image.removeAttribute("src");
      image.hidden = !(localPreview || content.image);
    }
  }

  function drawStatus() {
    const deviceStatus = container.querySelector("[data-page-device-status]");
    if (deviceStatus) deviceStatus.innerHTML = statusStrip(snapshot);
    if (!container.querySelector("[data-device-label]")) return;
    const confirmed = snapshot.showcase.label;
    const reconciliationRequired = live && snapshot.showcase.labelReconciliationRequired;
    container.querySelector("[data-label-reconciliation]").innerHTML = reconciliationRequired
      ? `<div class="notice warning"><div><strong>Retained label needs an update</strong><p>${esc(caseName())} retains its previous acknowledged label. Send the current exhibit label and wait for device acknowledgement to reconcile it with the applied profile.</p></div></div>`
      : "";
    const history = [...(label?.history || [])].sort((a, b) =>
      b.requestedAt.localeCompare(a.requestedAt),
    );
    const pendingOperation = snapshot.operations.find(
      (operation) =>
        operation.type === "label" &&
        ["pending", "recovery_pending"].includes(operation.status),
    );
    const pending = Boolean(pendingOperation);
    const assigned = snapshot.showcase.exhibitId === selectedId;
    const savedMatches = label?.draft && same(content, label.draft);
    const deliveredMatches = Boolean(
      confirmed &&
        confirmed.exhibitId === selectedId &&
        same(content, confirmed.content),
    );
    const matchesPending =
      pendingOperation && pendingOperation.targetId === selectedId && same(content, pendingOperation.payload.content);
    const matchesFailed =
      history[0]?.status === "failed" &&
      same(content, history[0].payload.content);
    const draftState = savedMatches
      ? `Draft saved${label.savedAt ? ` ${formatTime(label.savedAt)}` : ""}`
      : "Local draft not saved";
    const recoveryMessage = pendingOperation?.status === "recovery_pending"
      ? "Label recovery pending. The command remains locked until device confirmation."
      : live && pendingOperation?.executeBefore && Date.parse(pendingOperation.executeBefore) <= Date.now()
        ? "Label execution deadline passed. The command remains locked; review recovery in Activity."
        : null;
    const editorStatus = !canOperate
      ? (savedMatches ? `${draftState} · Read-only preview` : "Read-only preview of label content")
      : recoveryMessage
      ? `${recoveryMessage} · ${draftState}`
      : matchesPending
      ? `${live ? "Device delivery" : "Mock delivery"} pending · ${draftState}`
      : matchesFailed
        ? `${live ? "Device delivery" : "Mock delivery"} failed · ${draftState}`
        : deliveredMatches
          ? `${draftState} · Matches acknowledged content`
          : dirty()
            ? "Unsaved changes · Preview only"
            : savedMatches
              ? `${draftState} · Not sent to showcase`
              : "Editing label content · Save a draft or send an update";
    container.querySelector("[data-draft-status]").innerHTML =
      `<div class="notice ${matchesPending || matchesFailed ? "warning" : "info"}">${icon("report")}<span>${esc(editorStatus)}</span></div>`;
    const online = snapshot.showcase.online;
    container.querySelector("[data-save]").disabled = !canOperate || busy;
    container.querySelector("[data-send]").disabled =
      !canOperate || busy || pending || !assigned || !online;
    container.querySelector("#label-exhibit").disabled = busy;
    container.querySelector("[data-send-help]").textContent = !canOperate
      ? "An Operator or Administrator can edit drafts and send label updates."
      : !assigned
      ? `Assign this exhibit to ${caseName()} before sending its label.`
      : !online
        ? `${caseName()} is offline. You can save a draft; delivery is unavailable.`
        : pending
          ? recoveryMessage || `${live ? "Device delivery" : "Mock delivery"} pending. The previous acknowledged label remains on the showcase.`
          : reconciliationRequired
            ? "The retained label needs a current send and device acknowledgement. Saving a draft does not reconcile it."
          : matchesFailed
            ? "The previous update failed. Review its error in the history, then resend when ready."
            : `${live ? "Send creates a device request" : "Send creates a mock device request"}. Draft saving and device delivery are separate.`;
    container.querySelector("[data-device-label]").innerHTML = confirmed
      ? `<div class="acknowledged-label">${icon("check")}<div><strong>Latest update acknowledged</strong><p class="muted">${esc(formatDate(confirmed.acknowledgedAt))}, ${esc(formatTime(confirmed.acknowledgedAt))}</p></div></div><div class="confirmed-label-summary"><img ${imageAttributes(confirmed.content.image)} alt=""><div><strong>${esc(confirmed.content.title)}</strong><p>${esc(confirmed.content.collection)}</p><p class="muted">${esc(confirmed.content.description)}</p></div></div><p class="muted small-text">${!online ? "Device offline · Last acknowledged state shown. " : ""}${deliveredMatches ? "The preview matches the acknowledged label." : "The preview above has not been acknowledged on the showcase."}</p>`
      : `<div class="empty">No label has been acknowledged on the showcase.</div>`;
    container.querySelector("[data-label-history]").innerHTML = history.length
      ? `<div class="table-wrap"><table><thead><tr><th>Time (HKT)</th><th>Exhibit</th><th>Action</th><th>Status</th></tr></thead><tbody>${history.map((operation) => `<tr><td>${esc(formatDate(operation.requestedAt))}<br>${esc(formatTime(operation.requestedAt))}</td><td>${esc(exhibit().name)}</td><td>${live ? "Label update" : "Mock label update"}${operation.error ? `<small class="history-error">${esc(operation.error)}</small>` : ""}</td><td>${badge(operation.status === "acknowledged" ? "Acknowledged" : operation.status === "failed" ? "Failed" : operation.status === "recovery_pending" ? "Recovery pending" : operation.status === "recovered" ? "Recovered" : "Pending", statusTone(operation.status))}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty compact-empty">No label requests for this exhibit yet.</div>`;
  }

  async function refreshStatus() {
    const current = ++revision;
    const forId = selectedId;
    try {
      const [nextSnapshot, nextLabel] = await Promise.all([
        ctx.api.getSnapshot({ include: ["exhibits", "operations"] }),
        forId ? ctx.api.getLabel(forId) : Promise.resolve(null),
      ]);
      if (disposed || current !== revision || forId !== selectedId) return;
      snapshot = nextSnapshot;
      label = nextLabel;
      if (!canOperate && container.querySelector("form")) {
        content = { ...initialContent() };
        baseline = { ...content };
        const form = container.querySelector("form");
        for (const name of ["title", "collection", "description"])
          form.elements[name].value = content[name];
        drawPreview();
      }
      drawStatus();
    } catch (error) {
      if (!disposed) toast(message(error), "error");
    }
  }
  function validatedContent() {
    const next = {
      ...content,
      title: content.title.trim(),
      collection: content.collection.trim(),
      description: content.description.trim(),
    };
    if (!next.title || (!live && !next.collection) || !next.description)
      throw new Error(live ? "Enter a title and description." : "Enter a title, collection, and short description.");
    return next;
  }
  async function saveDraft() {
    if (!ctx.api.can("operate")) return;
    if (busy) return;
    if (!container.querySelector("form").reportValidity()) return;
    container.querySelector("[data-form-error]").hidden = true;
    busy = true;
    drawStatus();
    try {
      const beforeSave = { ...content };
      const saving = validatedContent();
      const result = await ctx.api.saveLabelDraft(selectedId, live ? { ...saving, version: editVersion } : saving);
      if (live) editVersion = result?.draft?.version ?? result?.version ?? result?.draftVersion ?? editVersion + 1;
      if (disposed) return;
      // Preserve edits made while the asynchronous save was running.
      if (same(content, beforeSave)) {
        content = { ...saving };
        const form = container.querySelector("form");
        for (const name of ["title", "collection", "description"])
          form.elements[name].value = content[name];
        drawPreview();
      }
      if (live) { saving.version = editVersion; content.version = editVersion; }
      baseline = { ...saving };
      ctx.setDirty(dirty());
      await refreshStatus();
      toast("Label draft saved. It has not been sent.");
    } catch (error) {
      showError(error);
    } finally {
      busy = false;
      drawStatus();
    }
  }
  async function sendLabel(event) {
    event.preventDefault();
    if (!ctx.api.can("operate")) return;
    if (busy) return;
    container.querySelector("[data-form-error]").hidden = true;
    const sendId = selectedId;
    let sending;
    try {
      sending = validatedContent();
    } catch (error) {
      showError(error);
      return;
    }
    if (
      !(await confirmAction(
        `Send label to ${caseName()}?`,
        `Request delivery of “${sending.title}”? The current label remains until the ${live ? "showcase" : "mock showcase"} acknowledges the update.`,
        "Send label",
      ))
    )
      return;
    if (disposed || sendId !== selectedId) return;
    busy = true;
    drawStatus();
    try {
      await ctx.api.sendLabel(sendId, live ? { ...sending, version: editVersion } : sending);
      toast(
        live ? "Label update requested. Waiting for device acknowledgement." : "Mock label update requested. Waiting for acknowledgement.",
        "info",
      );
      await refreshStatus();
      if (live) { editVersion = label?.draft?.version ?? label?.draftVersion ?? editVersion; content.version = editVersion; }
    } catch (error) {
      showError(error);
    } finally {
      busy = false;
      drawStatus();
    }
  }
  const beforeUnload = (event) => {
    if (dirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  window.addEventListener("beforeunload", beforeUnload);
  draw();
  stopSubscription = ctx.api.subscribe(refreshStatus);
  return () => {
    disposed = true;
    stopSubscription();
    window.removeEventListener("beforeunload", beforeUnload);
    ctx.setDirty(false);
  };
}
