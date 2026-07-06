import { api } from "../../core/api.js";

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

let presets = [];
let editingPresetName = null;

export function loadPresets() {
  return api("/api/presets").then((data) => {
    presets = data.presets || [];
    return presets;
  });
}

export function openPresetsModal() {
  loadPresets().then(() => {
    renderPresetsList();
    clearPresetForm();
    document.getElementById("presets-modal").classList.remove("is-hidden");
  });
}

export function closePresetsModal() {
  document.getElementById("presets-modal").classList.add("is-hidden");
  editingPresetName = null;
  clearPresetForm();
}

function renderPresetsList() {
  const container = document.getElementById("presets-list");
  container.innerHTML = "";

  if (!presets.length) {
    container.innerHTML = '<p style="text-align:center;opacity:0.6;padding:1rem;">No presets yet. Create one below.</p>';
    return;
  }

  presets.forEach((p) => {
    const row = document.createElement("div");
    row.className = "config-row";
    row.style.cursor = "pointer";
    row.style.padding = "0.5rem 0";
    row.innerHTML = `
      <span class="config-key" style="font-weight:600;">✦ ${escapeHtml(p.name)}</span>
      <span class="config-value">${p.description ? escapeHtml(p.description) : ""}</span>
    `;
    row.addEventListener("click", () => editPreset(p.name));
    container.appendChild(row);
  });
}

function editPreset(name) {
  const p = presets.find((x) => x.name === name);
  if (!p) return;
  editingPresetName = name;
  document.getElementById("preset-form-title").textContent = "Edit Preset";
  document.getElementById("preset-name").value = p.name;
  document.getElementById("preset-description").value = p.description || "";
  document.getElementById("preset-context-window").value = p.context_window ?? "";
  document.getElementById("preset-parallel").value = p.parallel ?? "";
  document.getElementById("preset-extra-args").value = (p.extra_args || []).join("\n");
  document.getElementById("preset-system-prompt").value = p.system_prompt || "";
  document.getElementById("preset-delete-btn").style.display = "inline-block";
}

function clearPresetForm() {
  editingPresetName = null;
  document.getElementById("preset-form-title").textContent = "Create Preset";
  document.getElementById("preset-name").value = "";
  document.getElementById("preset-description").value = "";
  document.getElementById("preset-context-window").value = "";
  document.getElementById("preset-parallel").value = "";
  document.getElementById("preset-extra-args").value = "";
  document.getElementById("preset-system-prompt").value = "";
  document.getElementById("preset-delete-btn").style.display = "none";
}

export function savePreset() {
  const name = document.getElementById("preset-name").value.trim();
  if (!name) return;

  const payload = {
    name,
    description: document.getElementById("preset-description").value.trim() || null,
    context_window: parseInt(document.getElementById("preset-context-window").value) || null,
    parallel: parseInt(document.getElementById("preset-parallel").value) || null,
    extra_args: document.getElementById("preset-extra-args").value
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean),
    system_prompt: document.getElementById("preset-system-prompt").value.trim() || null,
  };

  return api("/api/presets", {
    method: "POST",
    body: JSON.stringify(payload),
  }).then(() => {
    return loadPresets().then(() => {
      renderPresetsList();
      if (editingPresetName) {
        clearPresetForm();
      }
      // Refresh the preset select in the create/edit modal
      populatePresetSelect();
    });
  });
}

export function deletePreset() {
  if (!editingPresetName) return;
  return api(`/api/presets/${encodeURIComponent(editingPresetName)}`, {
    method: "DELETE",
  })
    .then(() => loadPresets().then(() => { renderPresetsList(); clearPresetForm(); }))
    .catch((err) => {
      if (err.status === 409) {
        alert(`Cannot delete: ${err.detail}`);
      } else {
        throw err;
      }
    });
}

export function populatePresetSelect() {
  return loadPresets().then(() => {
    const select = document.getElementById("edit-preset");
    if (!select) return;
    select.innerHTML = '<option value></option>';
    presets.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.name;
      opt.textContent = p.description ? `${p.name} – ${p.description}` : p.name;
      select.appendChild(opt);
    });
  });
}

export function bindEvents() {
  // Presets button
  const presetsBtn = document.getElementById("presets-btn");
  if (presetsBtn) {
    presetsBtn.addEventListener("click", openPresetsModal);
  }

  // Modal actions
  document.addEventListener("click", (e) => {
    const action = e.target.dataset.action;
    if (action === "presets-close") closePresetsModal();
    if (action === "preset-save") savePreset().catch((err) => alert(err.detail || err.message));
    if (action === "preset-delete") deletePreset().catch((err) => alert(err.detail || err.message));
    if (action === "preset-cancel") clearPresetForm();
  });
}
