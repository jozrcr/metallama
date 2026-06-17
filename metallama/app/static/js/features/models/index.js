import { api } from "../../core/api.js";
import { copyToClipboard } from "../../core/clipboard.js";
import { setConfigMessage } from "../../core/uiMessage.js";

const modelsEl = document.getElementById("models");
const summaryEl = document.getElementById("summary");

const inFlight = new Set();
const cardErrors = new Map();

// ── Edit Modal State ──────────────────────────────────────
let editingModelId = null;

function openEditModal(modelId) {
  const model = (async () => {
    const data = await api(`/api/models/${modelId}/status`);
    return data;
  })();

  model.then((data) => {
    editingModelId = modelId;
    document.getElementById("modal-title").textContent = `Edit: ${data.display_name || data.id}`;
    document.getElementById("edit-name").value = data.display_name || data.id || "";
    document.getElementById("edit-port").value = data.port || "";
    document.getElementById("edit-context-window").value = data.context_window || "";
    document.getElementById("edit-parallel").value = data.parallel || "";
    document.getElementById("edit-extra-args").value = (data.extra_args || []).join("\n");
    document.getElementById("edit-modal").classList.remove("is-hidden");
  });
}

function closeEditModal() {
  document.getElementById("edit-modal").classList.add("is-hidden");
  editingModelId = null;
}

async function saveEditModal() {
  if (!editingModelId) return;
  const payload = {
    name: document.getElementById("edit-name").value.trim(),
    port: parseInt(document.getElementById("edit-port").value, 10),
    context_window: parseInt(document.getElementById("edit-context-window").value, 10),
    parallel: parseInt(document.getElementById("edit-parallel").value, 10),
    extra_args: document.getElementById("edit-extra-args").value
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean),
  };

  // Remove undefined/NaN values
  Object.keys(payload).forEach((key) => {
    if (key === "extra_args" || key === "name") return;
    if (isNaN(payload[key])) delete payload[key];
  });
  if (payload.name === "") delete payload.name;

  try {
    setCardError(editingModelId, "");
    await api(`/api/models/${editingModelId}/config`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setConfigMessage("Config updated");
    closeEditModal();
    await refreshModels();
  } catch (err) {
    setCardError(editingModelId, err.message);
    setConfigMessage(err.message, true);
  }
}

function setCardError(modelId, message = "") {
  if (!modelId) {
    return;
  }
  const normalized = String(message || "").trim();
  if (!normalized) {
    cardErrors.delete(modelId);
    return;
  }
  cardErrors.set(modelId, normalized);
}

function canStart(model) {
  return model.status === "stopped" && !inFlight.has(model.id);
}

function canStop(model) {
  return model.status === "running" && !inFlight.has(model.id);
}

function modelTypeLabel(model) {
  const normalized = String(model.service || "").trim().toUpperCase();
  if (normalized === "LLM") return "LLM";
  return "LLM";
}

function cardAccentColor(managed) {
  return managed ? "#3B95DD" : "#8B5CF6";
}

function cardTemplate(model) {
  const isManaged = model.managed !== false;
  const action = model.status === "running" ? "stop" : "start";
  const label = action === "stop" ? "Stop" : "Start";
  const canRunAction = action === "stop" ? canStop(model) : canStart(model);
  const type = modelTypeLabel(model);
  const cardError = cardErrors.get(model.id) || "";
  const cardErrorClass = cardError ? "card-error visible" : "card-error";
  const accent = cardAccentColor(isManaged);
  const isLoading = inFlight.has(model.id);
  const overlayClass = isLoading ? "panel-overlay card-overlay" : "panel-overlay card-overlay is-hidden";
  const statusText = action === "start" ? "Starting..." : "Stopping...";

  const isLLM = type === "LLM";
  const ctxValue = model.context_window || "";
  const ctxKTokens = ctxValue ? Math.round(ctxValue / 1000) : "";
  const parValue = model.parallel || "";
  const ctxDisplay =
    isLLM
      ? `
    <span class="info-item">CTX: ${ctxKTokens}k</span>
    ${parValue ? `<span class="info-item">PAR: ${parValue}</span>` : ""}
  `
      : "";

  const modelWarning = model.model_found === false
    ? `<p class="model-not-found-warning">⚠ Model weights not found — server cannot start</p>`
    : "";

  return `
    <article class="card ${model.status}" data-model-id="${model.id}" style="--card-accent: ${accent}">
      <div class="card-header-row">
        <div class="title-wrap">
          <h3>${model.display_name}</h3>
        </div>
        <div class="status-badge ${model.status}">${model.status}</div>
      </div>

      <div class="card-main-row">
        <div class="card-meta-col">
          <div class="endpoint-row">
            <span class="endpoint-label">URL:</span>
            <a class="endpoint-link" href="${model.url}" target="_blank">${model.url}</a>
          </div>

          <div class="info-row">
            ${model.port ? `<span class="info-item">PORT: ${model.port}</span>` : ""}
            ${isManaged ? `<span class="info-item">PID: ${model.pid ?? "-"}</span>` : ""}
            ${ctxDisplay}
            ${isManaged ? `<button class="btn-secondary btn-small" data-id="${model.id}" data-action="cmd" title="Copy launch command">CMD</button>` : ""}
            ${isManaged ? `<button class="btn-secondary btn-small" data-id="${model.id}" data-action="edit" title="Edit server config">Edit</button>` : ""}
          </div>
        </div>

        <p class="description">${model.description || ""}</p>

        <div class="card-actions-col">
          ${isManaged ? `<button class="btn-action-${action}" data-id="${model.id}" data-action="${action}" ${canRunAction ? "" : "disabled"}>${label}</button>` : ""}
        </div>
      </div>

      <p class="${cardErrorClass}" aria-live="polite">${cardError}</p>
      ${modelWarning}

      <div class="${overlayClass}">
        <div class="overlay-content">
          <div class="spinner"></div>
          <p class="overlay-status">${statusText}</p>
        </div>
      </div>
    </article>
  `;
}

function renderModels(models) {
  if (!modelsEl || !summaryEl) {
    return;
  }

  modelsEl.innerHTML = models.map(cardTemplate).join("");
  const running = models.filter((m) => m.status === "running").length;
  summaryEl.textContent = `${running} / ${models.length} ACTIVE SERVERS`;
}

export async function refreshModels() {
  const activeElement = document.activeElement;
  if (activeElement && (activeElement.classList?.contains("ctx-inline-input") || activeElement.classList?.contains("par-inline-input"))) {
    return;
  }

  const data = await api("/api/models");
  renderModels(data.models || []);
}

async function restartModel(modelId) {
  inFlight.add(modelId);
  await refreshModels();

  try {
    await api(`/api/models/${modelId}/stop`, { method: "POST" });
    setCardError(modelId, "");

    for (let i = 0; i < 60; i++) {
      const data = await api(`/api/models/${modelId}/status`);
      if (data.status === "stopped") {
        break;
      }
      await new Promise((r) => setTimeout(r, 500));
    }

    await api(`/api/models/${modelId}/start`, { method: "POST" });

    for (let i = 0; i < 60; i++) {
      const data = await api(`/api/models/${modelId}/status`);
      if (data.status === "running") {
        break;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  } finally {
    inFlight.delete(modelId);
    await refreshModels();
  }
}

async function startStop(modelId, action) {
  if (action === "restart") {
    return restartModel(modelId);
  }

  const targetStatus = action === "start" ? "running" : "stopped";
  inFlight.add(modelId);
  await refreshModels();
  try {
    await api(`/api/models/${modelId}/${action}`, { method: "POST" });
    setCardError(modelId, "");
    for (let i = 0; i < 60; i++) {
      const data = await api(`/api/models/${modelId}/status`);
      if (data.status === targetStatus) {
        break;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  } finally {
    inFlight.delete(modelId);
    await refreshModels();
  }
}

export function setupModels() {
  if (!modelsEl) {
    return;
  }

  modelsEl.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLButtonElement)) {
      return;
    }

    const modelId = target.dataset.id;
    const action = target.dataset.action;
    const url = target.dataset.url;
    if (!modelId || !action) {
      return;
    }

    try {
      if (action === "copy") {
        if (!url) {
          throw new Error("Missing URL");
        }
        await copyToClipboard(url);
        setConfigMessage("Endpoint copied");
        return;
      }

      if (action === "cmd") {
        const data = await api(`/api/models/${modelId}/command`);
        await copyToClipboard(data.command);
        setCardError(modelId, "");
        setConfigMessage("Launch command copied to clipboard");
        return;
      }

      if (action === "open") {
        if (!url) {
          throw new Error("Missing URL");
        }
        window.open(url, "_blank", "noopener,noreferrer");
        return;
      }

      if (action === "edit") {
        openEditModal(modelId);
        return;
      }

      await startStop(modelId, action);
    } catch (err) {
      setCardError(modelId, err.message);
      await refreshModels();
      setConfigMessage(err.message, true);
    }
  });

  // ── Modal event listeners ──────────────────────────────
  const modal = document.getElementById("edit-modal");
  if (modal) {
    modal.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLButtonElement)) return;
      const action = target.dataset.action;
      if (action === "modal-close" || action === "modal-cancel") {
        closeEditModal();
      } else if (action === "modal-save") {
        saveEditModal();
      }
    });

    // Close on overlay click (outside dialog)
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeEditModal();
      }
    });

    // Close on Escape key
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.classList.contains("is-hidden")) {
        closeEditModal();
      }
    });
  }
}
