const stateLabels = {
  uploaded: "Загружен", inspecting: "Инспекция", inspection_failed: "Ошибка инспекции",
  duplicate_review: "Проверка дубля", ready_for_analysis: "Готов к анализу", analyzing: "Анализ",
  analysis_failed: "Ошибка анализа", awaiting_metadata_review: "Проверка метаданных",
  awaiting_narration_review: "Проверка сценария", generating_narration: "Озвучка", master_building: "Сборка master",
  master_failed: "Ошибка master", master_ready: "Master готов", transcribing: "Транскрибация",
  transcription_failed: "Ошибка ASR", aligning: "Выравнивание", alignment_failed: "Ошибка таймингов",
  packaging: "Упаковка", validation_failed: "Ошибка валидации", awaiting_qa: "QA-проверка",
  validated: "Проверен", delivery_queued: "В очереди на доставку", uploading_media: "Загрузка media",
  uploading_sidecars: "Загрузка sidecars", verifying_delivery: "Сверка доставки", delivery_failed: "Ошибка доставки",
  complete: "Завершён", cancelled: "Отменён"
};

const jobLabels = {
  inspect_asset: "Инспекция медиа", analyze_video: "Анализ видео", generate_narration: "Генерация озвучки",
  build_master: "Сборка master", transcribe_master: "Транскрибация", build_package: "Сборка комплекта",
  export_package: "ZIP-экспорт", deliver_package: "Облачная доставка"
};

const pipelineGroups = [
  ["Приём", ["uploaded", "inspecting", "inspection_failed", "duplicate_review"]],
  ["Анализ", ["ready_for_analysis", "analyzing", "analysis_failed", "awaiting_metadata_review"]],
  ["Подготовка", ["awaiting_narration_review", "generating_narration", "master_building", "master_failed", "master_ready", "transcribing", "transcription_failed", "aligning", "alignment_failed"]],
  ["QA и упаковка", ["packaging", "validation_failed", "awaiting_qa", "validated"]],
  ["Доставка", ["delivery_queued", "uploading_media", "uploading_sidecars", "verifying_delivery", "delivery_failed", "complete"]]
];

const appState = { packages: [], assets: new Map(), selectedId: null, busy: false };
const $ = (selector) => document.querySelector(selector);

function node(tag, className, text) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined) value.textContent = text;
  return value;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      const value = body.detail || body;
      detail = typeof value === "string" ? value : value.message || value.code || JSON.stringify(value);
    } catch (_) { /* response is not JSON */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function tone(value) {
  if (["complete", "validated", "succeeded", "approved", "ready"].includes(value)) return "success";
  if (value.includes("failed") || ["rejected", "not_ready"].includes(value)) return "error";
  if (["queued", "running", "inspecting", "analyzing", "transcribing", "packaging", "delivery_queued", "uploading_media", "uploading_sidecars", "verifying_delivery"].includes(value)) return "running";
  return "";
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "—";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function toast(message, isError = false) {
  const target = $("#toast");
  target.textContent = message;
  target.className = `toast${isError ? " error" : ""}`;
  target.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { target.hidden = true; }, 5000);
}

async function loadHealth() {
  const target = $("#health");
  try {
    const health = await api("/api/v1/health/ready");
    target.className = `health ${health.status === "ready" ? "ready" : "failed"}`;
    target.lastElementChild.textContent = health.status === "ready" ? "Система готова" : "Нужна проверка";
  } catch (_) {
    target.className = "health failed";
    target.lastElementChild.textContent = "API недоступен";
  }
}

async function loadPackages({ preserveSelection = true } = {}) {
  try {
    appState.packages = await api("/api/v1/packages");
    const query = $("#search").value.trim().toLowerCase();
    const assets = await Promise.all(appState.packages.map(async (item) => {
      try {
        const asset = await api(`/api/v1/assets/${item.source_asset_id}`);
        appState.assets.set(asset.id, asset);
        return asset;
      } catch (_) { return null; }
    }));
    void assets;
    updateStats();
    renderPackageList(query);
    if (!preserveSelection || !appState.packages.some((item) => item.id === appState.selectedId)) {
      appState.selectedId = location.hash.slice(1) || appState.packages[0]?.id || null;
    }
    if (appState.selectedId) await selectPackage(appState.selectedId, false);
  } catch (error) {
    toast(`Не удалось загрузить комплекты: ${error.message}`, true);
  }
}

function updateStats() {
  const finished = appState.packages.filter((item) => item.state === "complete").length;
  const active = appState.packages.filter((item) => !["complete", "cancelled"].includes(item.state)).length;
  $("#stat-total").textContent = appState.packages.length;
  $("#stat-active").textContent = active;
  $("#stat-ready").textContent = finished;
}

function renderPackageList(query = "") {
  const list = $("#package-list");
  list.replaceChildren();
  const filtered = appState.packages.filter((item) => {
    const asset = appState.assets.get(item.source_asset_id);
    return !query || item.id.toLowerCase().includes(query) || asset?.original_name.toLowerCase().includes(query);
  });
  if (!filtered.length) {
    list.append(node("div", "list-empty", appState.packages.length ? "Ничего не найдено." : "Пока нет комплектов. Загрузите первое видео."));
    return;
  }
  filtered.forEach((item) => {
    const asset = appState.assets.get(item.source_asset_id);
    const button = node("button", `package-item${item.id === appState.selectedId ? " active" : ""}`);
    button.type = "button";
    button.append(node("span", "package-name", asset?.original_name || `Комплект ${item.id.slice(0, 8)}`));
    button.append(node("span", `state ${tone(item.state)}`, stateLabels[item.state] || item.state));
    button.append(node("span", "package-id", item.id));
    button.addEventListener("click", () => selectPackage(item.id));
    list.append(button);
  });
}

async function selectPackage(packageId, updateHash = true) {
  appState.selectedId = packageId;
  if (updateHash) history.replaceState(null, "", `#${packageId}`);
  renderPackageList($("#search").value.trim().toLowerCase());
  const packageItem = appState.packages.find((item) => item.id === packageId);
  if (!packageItem) return;
  $("#empty-state").hidden = true;
  $("#package-detail").hidden = false;
  renderPackageShell(packageItem);
  try {
    const [asset, jobs, runs] = await Promise.all([
      api(`/api/v1/assets/${packageItem.source_asset_id}`),
      api(`/api/v1/packages/${packageId}/jobs`),
      api(`/api/v1/packages/${packageId}/analysis-runs`)
    ]);
    if (appState.selectedId !== packageId) return;
    appState.assets.set(asset.id, asset);
    renderPackageDetails(packageItem, asset, jobs, runs);
  } catch (error) {
    toast(`Ошибка карточки: ${error.message}`, true);
  }
}

function renderPackageShell(item) {
  const asset = appState.assets.get(item.source_asset_id);
  $("#detail-title").textContent = asset?.original_name || "Медиакомплект";
  $("#detail-id").textContent = item.id;
  $("#detail-kicker").textContent = stateLabels[item.state] || item.state;
  renderPipeline(item.state);
  renderActions(item);
}

function renderPipeline(state) {
  const currentIndex = Math.max(0, pipelineGroups.findIndex(([, states]) => states.includes(state)));
  const target = $("#pipeline");
  target.replaceChildren();
  pipelineGroups.forEach(([label], index) => {
    const className = index < currentIndex ? "pipeline-step done" : index === currentIndex ? "pipeline-step current" : "pipeline-step";
    target.append(node("div", className, label));
  });
}

function renderActions(item) {
  const target = $("#detail-actions");
  target.replaceChildren();
  const action = actionFor(item);
  if (!action) {
    target.append(node("span", "muted", "На этом этапе нужна ручная проверка"));
    return;
  }
  const button = node("button", "button button-primary", action.label);
  button.type = "button";
  button.disabled = appState.busy;
  button.addEventListener("click", () => action.run(item));
  target.append(button);
}

function actionFor(item) {
  if (["uploaded", "inspection_failed"].includes(item.state)) return { label: "Запустить инспекцию", run: startInspection };
  if (["ready_for_analysis", "analysis_failed"].includes(item.state)) return { label: "Запустить анализ", run: (value) => startJob(value, "analyze_video") };
  if (["master_ready", "transcription_failed", "alignment_failed"].includes(item.state)) return { label: "Создать транскрипт", run: (value) => callWorkflow(value, "transcribe") };
  if (item.state === "validation_failed") return { label: "Повторить сборку", run: (value) => callWorkflow(value, "build") };
  return null;
}

async function withBusy(callback) {
  if (appState.busy) return;
  appState.busy = true;
  const selected = appState.packages.find((item) => item.id === appState.selectedId);
  if (selected) renderActions(selected);
  try { await callback(); }
  catch (error) { toast(error.message, true); }
  finally {
    appState.busy = false;
    await loadPackages();
  }
}

async function startInspection(item) {
  await withBusy(async () => {
    const transitioned = await api(`/api/v1/packages/${item.id}/transitions`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: "inspecting", expected_version: item.version })
    });
    await startJob(transitioned, "inspect_asset", false);
    toast("Инспекция поставлена в очередь");
  });
}

async function startJob(item, kind, manageBusy = true) {
  const execute = async () => {
    await api(`/api/v1/packages/${item.id}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": `${kind}-${item.id}-${crypto.randomUUID()}` },
      body: JSON.stringify({ kind, payload: {} })
    });
    toast(`${jobLabels[kind]}: задание создано`);
  };
  return manageBusy ? withBusy(execute) : execute();
}

async function callWorkflow(item, endpoint) {
  await withBusy(async () => {
    await api(`/api/v1/packages/${item.id}/${endpoint}`, {
      method: "POST", headers: { "Idempotency-Key": `${endpoint}-${item.id}-${crypto.randomUUID()}` }
    });
    toast("Задание поставлено в очередь");
  });
}

function renderPackageDetails(item, asset, jobs, runs) {
  $("#detail-title").textContent = asset.original_name;
  const inspection = asset.inspection;
  const video = inspection?.streams?.find((stream) => stream.codec_type === "video");
  const audio = inspection?.streams?.find((stream) => stream.codec_type === "audio");
  $("#asset-badge").textContent = asset.duplicate_of ? "Дубль" : inspection ? "Проверен" : "Без ffprobe";
  $("#asset-badge").className = `badge ${asset.duplicate_of ? "error" : inspection ? "success" : ""}`;
  const facts = [
    ["Размер", formatBytes(asset.size_bytes)], ["Длительность", inspection ? `${inspection.duration.toFixed(2)} с` : "—"],
    ["Видео", video ? `${video.codec_name || "—"} · ${video.width || "?"}×${video.height || "?"}` : "—"], ["Аудио", audio?.codec_name || "нет"],
    ["SHA-256", `${asset.sha256.slice(0, 12)}…`], ["Обновлён", formatDate(item.updated_at)]
  ];
  const factsTarget = $("#asset-facts");
  factsTarget.replaceChildren();
  facts.forEach(([label, value]) => { factsTarget.append(node("dt", "", label), node("dd", "", value)); });
  renderJob(jobs[0]);
  renderHistory(jobs, runs);
}

function renderJob(job) {
  const badge = $("#job-badge");
  const target = $("#job-summary");
  target.replaceChildren();
  if (!job) {
    badge.textContent = "Нет";
    badge.className = "badge";
    target.append(node("p", "muted", "Задания ещё не запускались."));
    return;
  }
  badge.textContent = job.state;
  badge.className = `badge ${tone(job.state)}`;
  target.append(node("div", "job-name", jobLabels[job.kind] || job.kind));
  const track = node("div", "progress-track");
  const fill = node("span"); fill.style.width = `${job.progress}%`; track.append(fill); target.append(track);
  const meta = node("div", "job-meta"); meta.append(node("span", "", `${job.progress}%`), node("span", "", formatDate(job.updated_at))); target.append(meta);
  if (job.error_message) target.append(node("div", "error-copy", `${job.error_code || "error"}: ${job.error_message}`));
}

function renderHistory(jobs, runs) {
  const target = $("#history");
  target.replaceChildren();
  $("#history-count").textContent = `${jobs.length} jobs · ${runs.length} analysis runs`;
  if (!jobs.length) { target.append(node("div", "muted", "История пока пуста.")); return; }
  jobs.forEach((job) => {
    const row = node("div", "history-row");
    row.append(node("span", `history-dot ${tone(job.state)}`));
    row.append(node("span", "history-kind", jobLabels[job.kind] || job.kind));
    row.append(node("span", "history-error", job.error_message || `${job.progress}% · ${job.state}`));
    row.append(node("time", "history-time", formatDate(job.created_at)));
    target.append(row);
  });
}

async function uploadFile(file) {
  if (!file || (!file.name.toLowerCase().endsWith(".mp4") && file.type !== "video/mp4")) {
    toast("Выберите MP4-файл", true); return;
  }
  const progress = $("#upload-progress");
  const button = $("#choose-file");
  progress.hidden = false; button.disabled = true;
  try {
    const form = new FormData(); form.append("file", file);
    const asset = await api("/api/v1/assets/uploads", { method: "POST", body: form });
    const packageItem = await api("/api/v1/packages", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_asset_id: asset.id })
    });
    appState.selectedId = packageItem.id;
    history.replaceState(null, "", `#${packageItem.id}`);
    toast(asset.duplicate_of ? "Загружение создано: найден точный дубль" : "Видео загружено, комплект создан");
    await loadPackages({ preserveSelection: true });
  } catch (error) { toast(`Ошибка загрузки: ${error.message}`, true); }
  finally { progress.hidden = true; button.disabled = false; $("#file-input").value = ""; }
}

function bindEvents() {
  const input = $("#file-input");
  const zone = $("#drop-zone");
  $("#choose-file").addEventListener("click", () => input.click());
  input.addEventListener("change", () => uploadFile(input.files[0]));
  ["dragenter", "dragover"].forEach((event) => zone.addEventListener(event, (value) => { value.preventDefault(); zone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach((event) => zone.addEventListener(event, (value) => { value.preventDefault(); zone.classList.remove("dragging"); }));
  zone.addEventListener("drop", (event) => uploadFile(event.dataTransfer.files[0]));
  $("#refresh").addEventListener("click", () => loadPackages());
  $("#search").addEventListener("input", (event) => renderPackageList(event.target.value.trim().toLowerCase()));
}

bindEvents();
loadHealth();
loadPackages({ preserveSelection: false });
setInterval(loadHealth, 15000);
setInterval(() => loadPackages(), 10000);
