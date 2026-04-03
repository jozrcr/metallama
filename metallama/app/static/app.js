const modelsEl = document.getElementById("models");
const configForm = document.getElementById("config-form");
const binaryPathInput = document.getElementById("binary-path");
const baseUrlInput = document.getElementById("base-url");
const configMessageEl = document.getElementById("config-message");
const summaryEl = document.getElementById("summary");

let inFlight = new Set();

function setConfigMessage(msg, isError = false) {
  configMessageEl.textContent = msg;
  configMessageEl.style.color = isError ? "#b00020" : "#0f5132";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    const detail = data.detail || `Request failed (${response.status})`;
    throw new Error(detail);
  }

  return data;
}

function canStart(model) {
  return model.status === "stopped" && !inFlight.has(model.id);
}

function canStop(model) {
  return model.status === "running" && !inFlight.has(model.id);
}

function formatTag(value) {
  return String(value || "").trim().toUpperCase();
}

function cardTemplate(model) {
  return `
    <article class="card">
      <div class="card-head">
        <h3>${model.display_name}</h3>
        <span class="status ${model.status}">${model.status}</span>
      </div>
      <div class="tags">
        <span class="tag">${formatTag(model.modality)}</span>
        <span class="tag">${formatTag(model.use_case)}</span>
        <span class="tag">${model.size}</span>
      </div>
      <p class="description">${model.description}</p>
      <p class="meta"><strong>Family:</strong> ${model.family}</p>
      <p class="meta"><strong>URL:</strong> ${model.url}</p>
      <p class="meta"><strong>Port:</strong> ${model.port}</p>
      <p class="meta"><strong>PID:</strong> ${model.pid ?? "-"}</p>
      <div class="actions">
        <button data-id="${model.id}" data-action="start" ${canStart(model) ? "" : "disabled"}>Start</button>
        <button data-id="${model.id}" data-action="stop" ${canStop(model) ? "" : "disabled"}>Stop</button>
      </div>
    </article>
  `;
}

function renderModels(models) {
  modelsEl.innerHTML = models.map(cardTemplate).join("");

  const running = models.filter((m) => m.status === "running").length;
  summaryEl.textContent = `${running}/${models.length} running`;
}

async function refreshModels() {
  const data = await api("/api/models");
  renderModels(data.models || []);
}

async function loadConfig() {
  const config = await api("/api/config");
  binaryPathInput.value = config.llamacpp_binary || "";
  baseUrlInput.value = config.base_url || "";
}

async function startStop(modelId, action) {
  inFlight.add(modelId);
  try {
    await api(`/api/models/${modelId}/${action}`, { method: "POST" });
  } finally {
    inFlight.delete(modelId);
    await refreshModels();
  }
}

modelsEl.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement)) {
    return;
  }

  const modelId = target.dataset.id;
  const action = target.dataset.action;
  if (!modelId || !action) {
    return;
  }

  try {
    await startStop(modelId, action);
  } catch (err) {
    setConfigMessage(err.message, true);
  }
});

configForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/config", {
      method: "POST",
      body: JSON.stringify({
        llamacpp_binary: binaryPathInput.value,
        base_url: baseUrlInput.value,
      }),
    });
    setConfigMessage("Config saved");
    await refreshModels();
  } catch (err) {
    setConfigMessage(err.message, true);
  }
});

async function init() {
  await loadConfig();
  await refreshModels();
  setInterval(() => {
    refreshModels().catch(() => {});
  }, 2000);
}

init().catch((err) => {
  setConfigMessage(err.message, true);
});
