import {
  esc,
  icon,
  toast,
  confirmAction,
  showDialog,
  fileToDataUrl,
  statusStrip,
} from "../ui.js";

const message = (error) =>
  error?.status === 409 || error?.code === "version_conflict" ? "This record changed on the server. Your edits are preserved. Copy your changes, then reopen the editor to load the current version before retrying." : error?.message || "The change could not be completed. Please try again.";
const exhibitImage = (exhibit) => exhibit.image || "assets/vase.svg";

export async function render(container, ctx) {
  const live = ctx.api.getMode?.() === "live";
  const canOperate = ctx.api.can("operate");
  let snapshot = await ctx.api.getSnapshot({ include: ["exhibits", "operations"] });
  let exhibits = snapshot.exhibits;
  const caseName = () => live ? snapshot.showcase.name || "Selected showcase" : "Case 01";
  let selectedId =
    exhibits.find((item) => item.id === ctx.params?.exhibitId)?.id ||
    exhibits.find((item) => item.id === snapshot.showcase.exhibitId)?.id ||
    exhibits[0]?.id ||
    null;
  let search = "",
    assignmentFilter = "all",
    disposed = false,
    revision = 0,
    profileDialog = null;
  container.innerHTML = `<div data-page-device-status>${statusStrip(snapshot)}</div><div class="exhibits-layout"><div class="stack"><section class="card exhibit-library"><div class="card-head"><h2>Exhibit library</h2><span class="muted" data-profile-count></span></div><div class="exhibit-library-tools"><label class="exhibit-search">${icon("search")}<input type="search" aria-label="Search exhibits by name or tag" placeholder="Search name or tag…"></label><select aria-label="Filter exhibits by assignment"><option value="all">All exhibits</option><option value="assigned">On display</option><option value="unassigned">Unassigned</option></select></div><div data-exhibit-list></div><button type="button" class="button primary exhibit-add">${icon("plus")} Add exhibit</button></section><section class="card tag-registration"><div class="tag-registration-intro"><span class="illustration-icon">${icon("scan")}</span><div><h3>Register an exhibit tag</h3><p class="muted">Scan a tag, review its profile, then confirm the assignment.</p></div></div><button type="button" class="button" data-scan>${icon("scan")} Start mock tag scan</button><p class="muted small-text">${icon("info")} Exhibit identification tags do not unlock the case.</p><div data-scan-status aria-live="polite"></div></section></div><section class="card exhibit-detail" data-exhibit-detail></section></div>`;
  if (!canOperate) {
    container.querySelector(".exhibit-add").remove();
    container.querySelector(".tag-registration").remove();
    container.insertAdjacentHTML("afterbegin", '<div class="notice info">Viewer access · Browse exhibit profiles and associated content. Changes require an Operator or Administrator.</div>');
  }
  if (live && canOperate) {
    const scan = container.querySelector("[data-scan]");
    scan.innerHTML = `${icon("scan")} Wait for hardware tag scan`;
    scan.disabled = !ctx.api.can("staff");
    container.querySelector(".tag-registration-intro p").textContent = ctx.api.can("staff") ? "Start waiting, scan the exhibit tag on the hardware, review its profile, then confirm assignment." : "Live tag scans are Administrator-only. Select an exhibit in the library to assign it manually.";
  }
  const list = container.querySelector("[data-exhibit-list]");
  const detail = container.querySelector("[data-exhibit-detail]");

  function drawList() {
    container.querySelector("[data-profile-count]").textContent =
      `${exhibits.length} profile${exhibits.length === 1 ? "" : "s"}`;
    const filtered = exhibits.filter(
      (exhibit) =>
        `${exhibit.name} ${exhibit.tagId}`
          .toLowerCase()
          .includes(search.toLowerCase()) &&
        (assignmentFilter === "all" ||
          (assignmentFilter === "assigned"
            ? exhibit.showcaseId
            : !exhibit.showcaseId)),
    );
    list.innerHTML = filtered.length
      ? filtered
          .map(
            (exhibit) =>
              `<button type="button" class="exhibit-row ${exhibit.id === selectedId ? "selected" : ""}" data-exhibit="${esc(exhibit.id)}" aria-pressed="${exhibit.id === selectedId}"><img src="${esc(exhibitImage(exhibit))}" alt=""><span><strong>${esc(exhibit.name)}</strong><span class="muted exhibit-tag">${esc(exhibit.tagId)}</span><span class="exhibit-assignment ${exhibit.showcaseId ? "on-display" : ""}"><i></i>${exhibit.showcaseId ? "On display" : "Unassigned"}</span></span></button>`,
          )
          .join("")
      : `<div class="empty">${exhibits.length ? "No exhibits match your search." : "No exhibit profiles yet. Add an exhibit to get started."}</div>`;
    list.querySelectorAll("[data-exhibit]").forEach((button) =>
      button.addEventListener("click", () => {
        selectedId = button.dataset.exhibit;
        drawList();
        drawDetail();
      }),
    );
  }

  function drawDetail() {
    const exhibit = exhibits.find((item) => item.id === selectedId);
    if (!exhibit) {
      detail.innerHTML = `<div class="empty">${icon("cube")}<h2>Select an exhibit</h2><p>Create or select a profile to review its monitoring limits and associated content.</p></div>`;
      return;
    }
    const assigned = Boolean(snapshot.showcase.id && exhibit.showcaseId === snapshot.showcase.id);
    const pending = snapshot.operations.find(
      (operation) =>
        operation.type === "assignment" && ["pending", "recovery_pending"].includes(operation.status),
    );
    const latest = [...snapshot.operations]
      .reverse()
      .find(
        (operation) =>
          operation.type === "assignment" && operation.targetId === exhibit.id,
      );
    const confirmed = snapshot.showcase.label?.exhibitId === exhibit.id;
    const appliedVersion = snapshot.showcase.confirmedProfileVersion;
    const profileMatches = !live || (Number.isInteger(exhibit.version) && exhibit.version === appliedVersion);
    const applyControl = canOperate ? `<button type="button" class="button primary" data-assign ${pending || !snapshot.showcase.id ? "disabled" : ""}>${icon("cube")} ${assigned ? "Apply updated profile" : `Assign to ${esc(caseName())}`}</button>` : "";
    const profileStatus = assigned
      ? profileMatches
        ? `<div class="notice info">${icon("check")}<div><strong>Profile applied to ${esc(caseName())}</strong><p>${live ? `Device acknowledged profile version ${esc(appliedVersion)}. ` : "Monitoring limits use this profile. "}Label delivery is managed separately.</p></div></div>`
        : `<div class="notice warning"><div><strong>Saved profile differs from applied device version</strong><p>Saved version ${esc(exhibit.version ?? "unavailable")} · Applied version ${esc(appliedVersion ?? "unavailable")}. Saving a profile does not update device monitoring limits. Apply the updated profile and wait for device acknowledgement.</p></div></div>${applyControl}`
      : !canOperate ? `<p class="muted small-text">This profile is not assigned to ${esc(caseName())}.</p>`
      : `${applyControl}<p class="muted small-text">${snapshot.showcase.id ? "Review and confirm before replacing the current assignment." : "No showcase is configured. An assignment cannot be requested."}</p>`;
    detail.innerHTML = `<div class="card-head"><div><h2>${esc(exhibit.name)}</h2><p class="muted small-text">Exhibit ID: ${esc(exhibit.id)}</p><p class="exhibit-assignment ${assigned ? "on-display" : ""}"><i></i>${assigned ? `On display · ${esc(caseName())}` : "Unassigned profile"}</p></div>${canOperate ? `<button type="button" class="button" data-edit>${icon("edit")} Edit profile</button>` : ""}</div><div class="exhibit-description"><img class="exhibit-main-image" src="${esc(exhibitImage(exhibit))}" alt="${esc(exhibit.name)}"><div><h3>Description</h3><p class="muted">${esc(exhibit.description || "No description added.")}</p><dl class="exhibit-facts"><div><dt>Exhibit tag</dt><dd>${esc(exhibit.tagId)}</dd></div><div><dt>Assigned showcase</dt><dd>${assigned ? esc(caseName()) : "Unassigned"}</dd></div></dl></div></div>${exhibit.imageWarning ? `<p class="notice warning">${esc(exhibit.imageWarning)} The saved image reference is preserved.</p>` : ""}<div class="exhibit-section"><h3>${live ? "Saved monitoring limits" : "Monitoring limits"}</h3><div class="limit-grid"><div class="limit-card">${icon("temperature")}<div><span>Temperature</span><strong>${esc(exhibit.temperatureMin)} – ${esc(exhibit.temperatureMax)} °C</strong></div></div><div class="limit-card brightness">${icon("sun")}<div><span>Relative brightness</span><strong>${esc(exhibit.brightnessMin)} – ${esc(exhibit.brightnessMax)} / 100</strong><small class="muted">Relative sensor scale</small></div></div></div><p class="muted limit-note">${icon("clock")} Warn after an out-of-range reading persists for ${esc(exhibit.warningSeconds)} seconds.</p></div><div class="exhibit-section"><h3>Associated content</h3><button type="button" class="content-link" data-label>${icon("tag")}<span>Electronic label</span><span class="content-status">${live && snapshot.showcase.labelReconciliationRequired ? "Retained label · needs current send" : confirmed ? "Device label acknowledged" : "Draft and delivery status"}</span><span>Open label ${icon("arrow")}</span></button><button type="button" class="content-link" data-model>${icon("cube")}<span>Illustrative 3D model</span><span class="muted content-status">Model metadata</span><span>Review model ${icon("arrow")}</span></button></div><div class="exhibit-apply">${profileStatus}<div data-assignment-status aria-live="polite">${pending ? `<div class="notice warning">${pending.status === "recovery_pending" ? "Assignment recovery pending. The command remains locked until device confirmation." : live && pending.executeBefore && Date.parse(pending.executeBefore) <= Date.now() ? "Assignment execution deadline passed. The command remains locked; review recovery in Activity." : `${live ? "Assignment" : "Mock assignment"} pending acknowledgement. The confirmed assignment is still shown.`}</div>` : latest?.status === "failed" ? `<div class="notice warning">Assignment failed: ${esc(latest.error || "Device did not acknowledge the request.")}</div>` : ""}</div></div>`;
    detail
      .querySelector("[data-edit]")
      ?.addEventListener("click", () => editProfile(exhibit));
    detail
      .querySelector("[data-label]")
      .addEventListener("click", () =>
        ctx.navigate("labels", { exhibitId: exhibit.id }),
      );
    detail
      .querySelector("[data-model]")
      .addEventListener("click", () =>
        showDialog(
          "Illustrative 3D model",
          `<div class="notice info">Model metadata is a demo placeholder. No model is sent to the showcase.</div><dl class="model-metadata"><div><dt>Linked exhibit</dt><dd>${esc(exhibit.name)}</dd></div><div><dt>Model file</dt><dd>No 3D file attached</dd></div><div><dt>Animation</dt><dd>Unavailable</dd></div><div><dt>Review status</dt><dd>Awaiting model and review</dd></div></dl><div class="actions"><button class="button" type="button" data-close>Close</button></div>`,
          (dialog) =>
            dialog
              .querySelector("[data-close]")
              .addEventListener("click", () => dialog.close()),
        ),
      );
    detail
      .querySelector("[data-assign]")
      ?.addEventListener("click", () => assign(exhibit));
  }

  async function reload() {
    const current = ++revision;
    try {
      const nextSnapshot = await ctx.api.getSnapshot({ include: ["exhibits", "operations"] });
      if (disposed || current !== revision) return;
      snapshot = nextSnapshot;
      exhibits = nextSnapshot.exhibits;
      container.querySelector("[data-page-device-status]").innerHTML =
        statusStrip(snapshot);
      drawList();
      drawDetail();
    } catch (error) {
      if (!disposed) toast(message(error), "error");
    }
  }

  async function assign(exhibit) {
    if (!ctx.api.can("operate")) return;
    const current = exhibits.find(
      (item) => item.id === snapshot.showcase.exhibitId,
    );
    if (
      !(await confirmAction(
        `${live && current?.id === exhibit.id ? "Apply updated profile to" : "Assign exhibit to"} ${caseName()}?`,
        `Apply “${exhibit.name}” and its monitoring limits to ${caseName()}${current && current.id !== exhibit.id ? `, replacing “${current.name}”` : ""}? Device limits change only after acknowledgement. The electronic label will need to be sent separately.`,
        "Confirm assignment",
      ))
    )
      return;
    try {
      await ctx.api.assignExhibit(exhibit.id);
      toast(live ? "Assignment requested. Waiting for device acknowledgement." : "Mock assignment requested. Waiting for acknowledgement.", "info");
      await reload();
    } catch (error) {
      toast(message(error), "error");
    }
  }

  function editProfile(exhibit = {}, scanned = false) {
    if (!ctx.api.can("operate")) return;
    let image = exhibit.image || "assets/vase.svg";
    const numberField = (name, label, fallback, step = "1") => {
      const [min, max] = name.startsWith("temperature")
        ? (live ? [-40, 125] : [-20, 80])
        : name === "warningSeconds"
          ? [1, 3600]
          : [0, 100];
      return `<label class="field">${label}<input name="${name}" type="number" min="${min}" max="${max}" step="${step}" required value="${esc(exhibit[name] ?? fallback)}"></label>`;
    };
    showDialog(
      exhibit.id ? "Edit exhibit profile" : "Add exhibit",
      `<form class="exhibit-profile-form"><div class="form-error notice warning" role="alert" hidden></div><label class="field">Exhibit ID<input name="stableId" readonly value="${esc(exhibit.id || "Generated when saved")}"></label><div class="form-grid"><label class="field">Exhibit name<input name="name" maxlength="${live ? 120 : 100}" required value="${esc(exhibit.name || "")}"></label><label class="field">Exhibit tag<input name="tagId" maxlength="${live ? 64 : 40}" pattern="${live ? '[A-Za-z0-9][A-Za-z0-9_-]{0,63}' : '[Ee][Xx]-[A-Za-z0-9-]+'}" required value="${esc(exhibit.tagId || "")}" placeholder="EX-017"></label><label class="field full-width">Description<textarea name="description" rows="3" maxlength="2000" ${live ? "" : "required"}>${esc(exhibit.description || "")}</textarea></label>${numberField("temperatureMin", "Minimum temperature (°C)", 20, "0.1")}${numberField("temperatureMax", "Maximum temperature (°C)", 26, "0.1")}${numberField("brightnessMin", "Minimum brightness (0–100)", 30)}${numberField("brightnessMax", "Maximum brightness (0–100)", 70)}${numberField("warningSeconds", "Warning delay (seconds)", 120)}<label class="field">${live ? "Local image preview" : "Profile image"}<input type="file" name="imageFile" accept="image/png,image/jpeg,image/webp"></label></div>${live ? '<p class="notice info">Preview only—not uploaded. Saving preserves the existing remote image reference.</p>' : ""}<img data-local-preview src="${esc(image)}" alt="Profile image preview" style="max-width:120px;max-height:100px;object-fit:contain"><p class="muted small-text">Brightness uses a relative sensor scale. Saving a profile does not send a label or change the showcase assignment.</p><div class="actions"><button class="button" type="button" data-cancel>Cancel</button><button class="button primary" type="submit">Save profile</button></div></form>`,
      (dialog) => {
        const form = dialog.querySelector("form");
        const errorBox = form.querySelector(".form-error");
        form.elements.imageFile.addEventListener("change", async (event) => {
          const file = event.target.files[0];
          if (!file || file.size > 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) return;
          const preview = await fileToDataUrl(file);
          if (dialog.isConnected) dialog.querySelector("[data-local-preview]").src = preview;
        });
        const fieldErrors = new Map();
        form.querySelectorAll("input,textarea").forEach((input) => {
          const error = document.createElement("span");
          error.className = "field-error";
          error.id = `profile-error-${input.name}`;
          input.setAttribute("aria-describedby", error.id);
          input.parentElement.append(error);
          fieldErrors.set(input.name, error);
          input.addEventListener("input", () => {
            input.removeAttribute("aria-invalid");
            error.textContent = "";
          });
        });
        function showErrors(error) {
          errorBox.textContent = message(error);
          errorBox.hidden = false;
          let first;
          for (const [name, detail] of Object.entries(error.fields || {})) {
            const field = name === "image" ? "imageFile" : name;
            const input = form.elements[field];
            const hint = fieldErrors.get(field);
            if (input && hint) {
              input.setAttribute("aria-invalid", "true");
              hint.textContent = detail;
              first ||= input;
            }
          }
          first?.focus();
        }
        profileDialog = dialog;
        let changed = false,
          saving = false,
          asking = false;
        const initialValues = JSON.stringify(
          [...new FormData(form)].map(([key, value]) => [
            key,
            typeof value === "string" ? value : `${value.name}:${value.size}`,
          ]),
        );
        const updateDirty = () => {
          changed =
            JSON.stringify(
              [...new FormData(form)].map(([key, value]) => [
                key,
                typeof value === "string"
                  ? value
                  : `${value.name}:${value.size}`,
              ]),
            ) !== initialValues;
          ctx.setDirty(changed);
        };
        form.addEventListener("input", updateDirty);
        form.addEventListener("change", updateDirty);
        const close = dialog.close.bind(dialog);
        dialog.close = async (reason) => {
          if (reason !== "force" && (saving || asking)) return;
          if (reason !== "force" && changed) {
            asking = true;
            const discard = await confirmAction(
              "Discard unsaved profile changes?",
              "The exhibit profile has not been saved. Discard these edits?",
              "Discard changes",
            );
            asking = false;
            if (!discard) return;
          }
          close();
        };
        dialog.addEventListener("cancel", (event) => {
          event.preventDefault();
          dialog.close();
        });
        dialog.addEventListener(
          "close",
          () => {
            if (profileDialog === dialog) profileDialog = null;
            ctx.setDirty(false);
          },
          { once: true },
        );
        form
          .querySelector("[data-cancel]")
          .addEventListener("click", () => dialog.close());
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          errorBox.hidden = true;
          fieldErrors.forEach((error) => {
            error.textContent = "";
          });
          form
            .querySelectorAll("[aria-invalid]")
            .forEach((input) => input.removeAttribute("aria-invalid"));
          const submit = form.querySelector("[type=submit]");
          submit.disabled = true;
          saving = true;
          try {
            const data = new FormData(form);
            const file = data.get("imageFile");
            if (file?.size) {
              if (
                !["image/png", "image/jpeg", "image/webp"].includes(
                  file.type,
                ) ||
                file.size > 1024 * 1024
              )
                throw Object.assign(
                  new Error(
                    "Choose a PNG, JPG, or WebP image smaller than 1 MB.",
                  ),
                  {
                    fields: {
                      imageFile:
                        "Choose a PNG, JPG, or WebP image smaller than 1 MB.",
                    },
                  },
                );
              if (!live) image = await fileToDataUrl(file);
            }
            const profile = {
              ...exhibit,
              image,
              name: String(data.get("name")).trim(),
              tagId: String(data.get("tagId")).trim(),
              description: String(data.get("description")).trim(),
            };
            for (const field of [
              "temperatureMin",
              "temperatureMax",
              "brightnessMin",
              "brightnessMax",
              "warningSeconds",
            ])
              profile[field] = Number(data.get(field));
            const fields = {};
            if (!profile.name) fields.name = "Enter an exhibit name.";
            if (!profile.tagId) fields.tagId = "Enter an exhibit tag.";
            if (live && /^CARD-/i.test(profile.tagId)) fields.tagId = "CARD- identifiers are reserved for staff access cards.";
            if (!live && !profile.description)
              fields.description = "Enter a description.";
            if (profile.temperatureMin >= profile.temperatureMax)
              fields.temperatureMin =
                "Minimum temperature must be below the maximum.";
            if (profile.brightnessMin >= profile.brightnessMax)
              fields.brightnessMin =
                "Minimum brightness must be below the maximum.";
            if (Object.keys(fields).length)
              throw Object.assign(new Error("Review the highlighted fields."), {
                fields,
              });
            const saved = await ctx.api.saveExhibit(profile);
            selectedId = saved.id;
            changed = false;
            ctx.setDirty(false);
            dialog.close("force");
            toast("Exhibit profile saved.");
            await reload();
            if (scanned) await assign(saved);
          } catch (error) {
            showErrors(error);
          } finally {
            submit.disabled = false;
            saving = false;
          }
        });
      },
    );
  }

  container
    .querySelector("input[type=search]")
    .addEventListener("input", (event) => {
      search = event.target.value;
      drawList();
    });
  container
    .querySelector(".exhibit-library-tools select")
    .addEventListener("change", (event) => {
      assignmentFilter = event.target.value;
      drawList();
    });
  container
    .querySelector(".exhibit-add")
    ?.addEventListener("click", () => editProfile());
  container
    .querySelector("[data-scan]")
    ?.addEventListener("click", async (event) => {
      if (!ctx.api.can("operate")) return;
      if (live && !ctx.api.can("staff")) return;
      const button = event.currentTarget;
      const status = container.querySelector("[data-scan-status]");
      button.disabled = true;
      status.textContent = live ? "Waiting for a new hardware tag scan…" : "Waiting for a mock exhibit tag…";
      try {
        const tag = await ctx.api.scanExhibitTag();
        status.textContent = `${live ? "Hardware tag detected" : "Mock tag detected"}: ${tag.id}`;
        const found = exhibits.find(
          (exhibit) => exhibit.tagId.toUpperCase() === tag.id.toUpperCase(),
        );
        if (found) {
          selectedId = found.id;
          drawList();
          drawDetail();
          await assign(found);
        } else editProfile({ tagId: tag.id }, true);
      } catch (error) {
        status.textContent = message(error);
      } finally {
        button.disabled = false;
      }
    });
  drawList();
  drawDetail();
  const unsubscribe = ctx.api.subscribe(reload);
  if (canOperate && ctx.params?.edit) {
    const selected = exhibits.find((item) => item.id === selectedId);
    if (selected) editProfile(selected);
  }
  return () => {
    disposed = true;
    unsubscribe();
    profileDialog?.close("force");
  };
}
