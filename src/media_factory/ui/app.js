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

const appState = {
  packages: [], assets: new Map(), selectedId: null, busy: false,
  apiKey: sessionStorage.getItem("mediaFactoryApiKey") || ""
};
const $ = (selector) => document.querySelector(selector);

function node(tag, className, text) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined) value.textContent = text;
  return value;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (appState.apiKey) headers.set("X-API-Key", appState.apiKey);
  const response = await fetch(path, { ...options, headers });
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

async function loadPrincipal() {
  try {
    const principal = await api("/api/v1/auth/me");
    $("#principal").textContent = `${principal.actor} · ${principal.role}`;
  } catch (_) { $("#principal").textContent = "требуется ключ"; }
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

async function loadMetrics() {
  try {
    const metrics = await api("/api/v1/metrics/summary");
    $("#stat-stuck").textContent = metrics.stuck_jobs.length;
    $("#stat-stuck").title = metrics.stuck_jobs.map((job) => `${job.kind}: ${job.id}`).join("\n");
  } catch (_) { $("#stat-stuck").textContent = "—"; }
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
    await loadMetrics();
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
    const [asset, jobs, runs, audit] = await Promise.all([
      api(`/api/v1/assets/${packageItem.source_asset_id}`),
      api(`/api/v1/packages/${packageId}/jobs`),
      api(`/api/v1/packages/${packageId}/analysis-runs`),
      api(`/api/v1/packages/${packageId}/audit-events?limit=50`)
    ]);
    if (appState.selectedId !== packageId) return;
    appState.assets.set(asset.id, asset);
    renderPackageDetails(packageItem, asset, jobs, runs, audit);
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
  if (item.state === "awaiting_metadata_review") return { label: "Проверить анализ", run: openReview };
  if (item.state === "awaiting_narration_review") return { label: "Подготовить аудио", run: openNarration };
  if (["master_building", "master_failed"].includes(item.state)) return { label: "Собрать master", run: (value) => callWorkflow(value, "build-master") };
  if (["master_ready", "transcription_failed", "alignment_failed"].includes(item.state)) return { label: "Создать транскрипт", run: (value) => callWorkflow(value, "transcribe") };
  if (["packaging", "validation_failed"].includes(item.state)) return { label: item.state === "packaging" ? "Собрать комплект" : "Повторить сборку", run: (value) => callWorkflow(value, "build") };
  if (item.state === "awaiting_qa") return { label: "Провести QA", run: openQa };
  if (["validated", "delivery_failed", "complete"].includes(item.state)) return { label: item.state === "complete" ? "Открыть результат" : "Экспорт и доставка", run: openRelease };
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

function renderPackageDetails(item, asset, jobs, runs, audit) {
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
  renderAudit(audit);
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

function renderAudit(events) {
  const target = $("#audit-history"); target.replaceChildren();
  $("#audit-count").textContent = `${events.length} событий`;
  if (!events.length) { target.append(node("div", "muted", "События появятся после первого изменения.")); return; }
  events.forEach((event) => {
    const row = node("div", "history-row");
    row.append(node("span", "history-dot success"));
    row.append(node("span", "history-kind", event.action));
    const detail = event.details.from && event.details.to
      ? `${event.details.from} → ${event.details.to}`
      : `${event.actor} · ${event.role}`;
    row.append(node("span", "history-error audit-copy", detail));
    row.append(node("time", "history-time", formatDate(event.occurred_at)));
    target.append(row);
  });
}

async function openReview(item) {
  const dialog = $("#review-dialog");
  $("#review-title").textContent = appState.assets.get(item.source_asset_id)?.original_name || "Анализ и метаданные";
  $("#review-body").replaceChildren(node("div", "review-empty", "Загрузка результатов…"));
  dialog.showModal();
  await loadReview(item);
}

async function loadReview(item) {
  try {
    const runs = await api(`/api/v1/packages/${item.id}/analysis-runs`);
    const run = runs[0];
    if (!run) throw new Error("Для комплекта нет analysis run");
    if (run.review_status === "pending") {
      const events = await api(`/api/v1/analysis-runs/${run.id}/events`);
      renderEventReview(item, run, events);
      return;
    }
    if (run.review_status === "rejected") {
      $("#review-body").replaceChildren(node("div", "review-empty", "Анализ отклонён. Комплект возвращён на повторный анализ."));
      return;
    }
    await loadMetadataReview(item, run);
  } catch (error) {
    $("#review-body").replaceChildren(node("div", "review-empty", error.message));
  }
}

function actionButton(label, className, callback) {
  const button = node("button", `button ${className}`, label);
  button.type = "button";
  button.addEventListener("click", callback);
  return button;
}

function renderEventReview(item, run, events) {
  $("#review-kicker").textContent = `Analysis run · ${run.provider_name} · ${run.prompt_version}`;
  const body = $("#review-body");
  body.replaceChildren();
  const intro = node("div", "review-intro");
  intro.append(node("p", "", "Проверьте evidence и таймкод каждого события. В метаданные попадут только принятые события."));
  const actions = node("div", "review-actions");
  const unresolved = events.filter((event) => !["approved", "rejected"].includes(event.review_status));
  if (unresolved.length) actions.append(actionButton("Принять все", "button-secondary", () => reviewAllEvents(item, run, unresolved)));
  actions.append(actionButton("Отклонить run", "button-secondary", () => reviewRun(item, run, false)));
  if (!unresolved.length) actions.append(actionButton("Утвердить run", "button-primary", () => reviewRun(item, run, true)));
  intro.append(actions); body.append(intro);
  const list = node("div", "event-list");
  events.forEach((event) => {
    const card = node("article", "event-card");
    card.append(node("div", "event-time", `${event.start.toFixed(2)} — ${event.end.toFixed(2)} с`));
    const main = node("div", "event-main");
    main.append(node("h4", "", `${event.actor} · ${event.action}`));
    main.append(node("p", "", event.evidence.join(" ") || "Evidence не указано"));
    const meta = node("div", "event-meta");
    [...event.objects, `confidence ${(event.confidence * 100).toFixed(0)}%`].forEach((value) => meta.append(node("span", "", value)));
    main.append(meta); card.append(main);
    if (["approved", "rejected"].includes(event.review_status)) {
      card.append(node("div", `event-status ${event.review_status}`, event.review_status === "approved" ? "Принято" : "Отклонено"));
    } else {
      const buttons = node("div", "event-buttons");
      const approve = node("button", "", "✓"); approve.title = "Принять";
      const reject = node("button", "", "×"); reject.title = "Отклонить";
      approve.addEventListener("click", () => reviewEvent(item, run, event, "approved"));
      reject.addEventListener("click", () => reviewEvent(item, run, event, "rejected"));
      buttons.append(approve, reject); card.append(buttons);
    }
    list.append(card);
  });
  body.append(list);
}

async function reviewEvent(item, run, event, reviewStatus) {
  try {
    await api(`/api/v1/analysis-events/${event.id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ review_status: reviewStatus, expected_version: event.version })
    });
    await loadReview(item);
  } catch (error) { toast(error.message, true); }
}

async function reviewAllEvents(item, run, events) {
  try {
    for (const event of events) {
      await api(`/api/v1/analysis-events/${event.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_status: "approved", expected_version: event.version })
      });
    }
    await loadReview(item);
  } catch (error) { toast(error.message, true); }
}

async function reviewRun(item, run, approved) {
  try {
    await api(`/api/v1/analysis-runs/${run.id}/reviews`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved })
    });
    if (approved) await loadMetadataReview(item, { ...run, review_status: "approved" });
    else { $("#review-dialog").close(); await loadPackages(); }
  } catch (error) { toast(error.message, true); }
}

async function loadMetadataReview(item, run) {
  try {
    const [versions, categories] = await Promise.all([
      api(`/api/v1/packages/${item.id}/metadata`), api("/api/v1/metadata/categories")
    ]);
    if (!versions.length) {
      const body = $("#review-body"); body.replaceChildren();
      const intro = node("div", "review-intro");
      intro.append(node("p", "", "Анализ утверждён. Создайте черновик метаданных из принятых событий."));
      intro.append(actionButton("Создать черновик", "button-primary", () => createMetadataProposal(item, run)));
      body.append(intro); return;
    }
    renderMetadataForm(item, run, versions.at(-1), categories);
  } catch (error) { toast(error.message, true); }
}

async function createMetadataProposal(item, run) {
  try {
    await api(`/api/v1/packages/${item.id}/metadata/proposals`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ analysis_run_id: run.id })
    });
    await loadMetadataReview(item, run);
  } catch (error) { toast(error.message, true); }
}

function formField(label, control) {
  const field = node("div", "field"); field.append(node("label", "", label), control); return field;
}

function renderMetadataForm(item, run, metadata, categories) {
  $("#review-kicker").textContent = `Метаданные · версия ${metadata.version}`;
  const body = $("#review-body"); body.replaceChildren();
  const form = node("form", "metadata-form");
  const title = node("input"); title.value = metadata.title; title.required = true; title.maxLength = 200;
  const description = node("textarea"); description.value = metadata.description; description.required = true; description.maxLength = 5000;
  const category = node("select"); categories.forEach((value) => { const option = node("option", "", value.label); option.value = value.code; option.selected = value.code === metadata.category; category.append(option); });
  const language = node("input"); language.value = metadata.narration_language || ""; language.placeholder = "ru, en-US или пусто";
  const chapters = node("textarea"); chapters.value = metadata.chapters.map((value) => `${value.start} | ${value.title}`).join("\n"); chapters.placeholder = "0 | Введение";
  const grid = node("div", "field-grid"); grid.append(formField("Категория", category), formField("Язык озвучки", language));
  form.append(formField("Заголовок", title), formField("Описание", description), grid, formField("Главы: секунда | название", chapters));
  form.append(node("div", "form-note", "Технические поля — duration, resolution, SHA-256 и WPM — будут вычислены из финальных артефактов и не редактируются вручную."));
  const actions = node("div", "review-actions");
  actions.append(actionButton("Сохранить новую версию", "button-secondary", () => saveMetadata(item, run, metadata, { title, description, category, language, chapters })));
  actions.append(actionButton("Утвердить версию", "button-primary", () => approveMetadata(metadata)));
  form.append(actions); body.append(form);
}

function parseChapters(value) {
  if (!value.trim()) return [];
  return value.split("\n").filter((line) => line.trim()).map((line) => {
    const separator = line.indexOf("|");
    if (separator < 1) throw new Error(`Неверная глава: ${line}`);
    const start = Number(line.slice(0, separator).trim());
    const title = line.slice(separator + 1).trim();
    if (!Number.isFinite(start) || start < 0 || !title) throw new Error(`Неверная глава: ${line}`);
    return { start, title };
  }).sort((left, right) => left.start - right.start);
}

async function saveMetadata(item, run, metadata, controls) {
  try {
    const payload = {
      base_version: metadata.version, title: controls.title.value.trim(), description: controls.description.value.trim(),
      category: controls.category.value, chapters: parseChapters(controls.chapters.value),
      narration_language: controls.language.value.trim() || null, created_by: "operator", change_note: "Edited in operator console"
    };
    await api(`/api/v1/metadata/${metadata.id}/revisions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    toast("Новая версия метаданных сохранена"); await loadMetadataReview(item, run);
  } catch (error) { toast(error.message, true); }
}

async function approveMetadata(metadata) {
  try {
    await api(`/api/v1/metadata/${metadata.id}/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: metadata.version })
    });
    $("#review-dialog").close(); toast("Метаданные утверждены"); await loadPackages();
  } catch (error) { toast(error.message, true); }
}

async function openNarration(item) {
  const dialog = $("#review-dialog");
  $("#review-title").textContent = appState.assets.get(item.source_asset_id)?.original_name || "Сценарий и аудио";
  $("#review-body").replaceChildren(node("div", "review-empty", "Загрузка сценария…"));
  dialog.showModal();
  await loadNarrationReview(item);
}

async function loadNarrationReview(item) {
  try {
    const [metadataVersions, scripts, tracks, decisions] = await Promise.all([
      api(`/api/v1/packages/${item.id}/metadata`),
      api(`/api/v1/packages/${item.id}/narration`),
      api(`/api/v1/packages/${item.id}/audio-tracks`),
      api(`/api/v1/packages/${item.id}/audio-decisions`)
    ]);
    const metadata = [...metadataVersions].reverse().find((value) => value.status === "approved");
    if (!metadata) throw new Error("Нет утверждённой версии метаданных");
    const script = scripts.at(-1);
    renderNarrationReview(item, metadata, script, tracks, decisions);
  } catch (error) {
    $("#review-body").replaceChildren(node("div", "review-empty", error.message));
  }
}

function renderNarrationReview(item, metadata, script, tracks, decisions) {
  $("#review-kicker").textContent = `Аудио · ${metadata.narration_language || "исходная дорожка"}`;
  const body = $("#review-body"); body.replaceChildren();
  if (decisions.length) {
    const latest = decisions.at(-1);
    body.append(node("div", "review-empty", `Решение по аудио «${latest.policy}» уже зафиксировано. Можно переходить к сборке master.`));
    return;
  }
  const intro = node("div", "review-intro");
  intro.append(node("p", "", metadata.narration_language
    ? "Отредактируйте текст, утвердите сценарий и создайте синтетическую дорожку."
    : "Для этих метаданных озвучка не запрошена. Выберите судьбу исходной аудиодорожки."));
  body.append(intro);

  if (!metadata.narration_language) {
    const actions = node("div", "review-actions");
    actions.append(actionButton("Сохранить исходное аудио", "button-primary", () => decideAudio(item, "preserve")));
    actions.append(actionButton("Удалить аудио", "button-secondary", () => decideAudio(item, "remove")));
    body.append(actions);
    return;
  }

  if (!script) {
    const actions = node("div", "review-actions");
    actions.append(actionButton("Создать сценарий из описания", "button-primary", () => createNarrationProposal(item, metadata)));
    body.append(actions);
    return;
  }

  const form = node("form", "metadata-form");
  const text = node("textarea"); text.value = script.text; text.required = true; text.maxLength = 20000; text.rows = 9;
  const language = node("input"); language.value = script.language; language.required = true;
  const style = node("input"); style.value = script.style; style.required = true;
  const wpm = node("input"); wpm.type = "number"; wpm.min = "60"; wpm.max = "240"; wpm.value = script.target_wpm;
  const grid = node("div", "field-grid");
  grid.append(formField("Язык", language), formField("Темп, слов/мин", wpm));
  form.append(formField("Текст озвучки", text), formField("Стиль", style), grid);
  form.append(node("div", "form-note", `Версия ${script.version} · ${script.status === "approved" ? "утверждена" : "черновик"}`));
  const scriptActions = node("div", "review-actions");
  if (script.status === "draft") {
    scriptActions.append(actionButton("Сохранить новую версию", "button-secondary", () => saveNarration(item, script, { text, language, style, wpm })));
    scriptActions.append(actionButton("Утвердить сценарий", "button-primary", () => approveNarration(item, script)));
  } else if (!tracks.length) {
    scriptActions.append(actionButton("Сгенерировать аудио", "button-primary", () => generateNarration(item, script)));
  }
  form.append(scriptActions); body.append(form);

  if (tracks.length) {
    const track = tracks.at(-1);
    const audio = node("section", "audio-choice");
    audio.append(node("h3", "", "Готовая дорожка"));
    audio.append(node("p", "muted", `${track.language} · ${track.duration.toFixed(2)} с · ${formatBytes(track.size_bytes)} · SHA ${track.sha256.slice(0, 12)}…`));
    const actions = node("div", "review-actions");
    actions.append(actionButton("Заменить аудио дорожкой TTS", "button-primary", () => decideAudio(item, "replace", track.id)));
    actions.append(actionButton("Оставить исходное", "button-secondary", () => decideAudio(item, "preserve")));
    actions.append(actionButton("Удалить аудио", "button-secondary", () => decideAudio(item, "remove")));
    audio.append(actions); body.append(audio);
  }
}

async function createNarrationProposal(item, metadata) {
  try {
    await api(`/api/v1/packages/${item.id}/narration/proposals`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ metadata_version_id: metadata.id, style: "neutral, factual", target_wpm: 130 })
    });
    toast("Черновик сценария создан"); await loadNarrationReview(item);
  } catch (error) { toast(error.message, true); }
}

async function saveNarration(item, script, controls) {
  try {
    await api(`/api/v1/narration/${script.id}/revisions`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_version: script.version, text: controls.text.value.trim(), language: controls.language.value.trim(),
        style: controls.style.value.trim(), target_wpm: Number(controls.wpm.value), created_by: "operator",
        change_note: "Edited in operator console"
      })
    });
    toast("Новая версия сценария сохранена"); await loadNarrationReview(item);
  } catch (error) { toast(error.message, true); }
}

async function approveNarration(item, script) {
  try {
    await api(`/api/v1/narration/${script.id}/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: script.version })
    });
    toast("Сценарий утверждён"); await loadNarrationReview(item);
  } catch (error) { toast(error.message, true); }
}

async function generateNarration(item, script) {
  try {
    await api(`/api/v1/packages/${item.id}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": `generate-narration-${item.id}-${crypto.randomUUID()}` },
      body: JSON.stringify({ kind: "generate_narration", payload: { script_id: script.id } })
    });
    $("#review-dialog").close(); toast("Озвучка поставлена в очередь"); await loadPackages();
  } catch (error) { toast(error.message, true); }
}

async function decideAudio(item, policy, audioTrackId = null) {
  try {
    await api(`/api/v1/packages/${item.id}/audio-decisions`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ policy, audio_track_id: audioTrackId, created_by: "operator" })
    });
    await api(`/api/v1/packages/${item.id}/build-master`, {
      method: "POST", headers: { "Idempotency-Key": `build-master-${item.id}-${crypto.randomUUID()}` }
    });
    $("#review-dialog").close(); toast("Решение по аудио сохранено, master поставлен в сборку"); await loadPackages();
  } catch (error) { toast(error.message, true); await loadPackages(); }
}

async function openQa(item) {
  const dialog = $("#review-dialog");
  $("#review-title").textContent = appState.assets.get(item.source_asset_id)?.original_name || "QA комплекта";
  $("#review-body").replaceChildren(node("div", "review-empty", "Загрузка сборки…"));
  dialog.showModal();
  try {
    const builds = await api(`/api/v1/packages/${item.id}/builds`);
    const build = builds.at(-1);
    if (!build) throw new Error("Сборка комплекта не найдена");
    renderQa(item, build);
  } catch (error) { $("#review-body").replaceChildren(node("div", "review-empty", error.message)); }
}

function renderQa(item, build) {
  $("#review-kicker").textContent = `QA · сборка v${build.version} · схема ${build.schema_version}`;
  const body = $("#review-body"); body.replaceChildren();
  const summary = node("div", "qa-summary");
  const metadata = build.computed_metadata || {};
  const facts = [
    ["Название", metadata.Title], ["Категория", metadata.Category], ["Длительность", metadata.Duration != null ? `${metadata.Duration.toFixed(2)} с` : "—"],
    ["Разрешение", metadata.Resolution], ["Язык", metadata.Language || "—"], ["WPM", metadata.WPM != null ? metadata.WPM.toFixed(1) : "—"]
  ];
  facts.forEach(([label, value]) => {
    const fact = node("div", "qa-fact"); fact.append(node("span", "", label), node("strong", "", value || "—")); summary.append(fact);
  });
  body.append(summary);
  const files = node("section", "qa-section"); files.append(node("h3", "", `Файлы · ${build.files.length}`));
  const fileList = node("div", "file-list");
  build.files.forEach((file) => {
    const row = node("div", "file-row");
    row.append(node("span", "file-role", file.role), node("span", "file-name", file.filename), node("span", "file-size", formatBytes(file.size_bytes)));
    fileList.append(row);
  });
  files.append(fileList); body.append(files);
  if (build.validation_issues.length) {
    const issues = node("section", "qa-section"); issues.append(node("h3", "", "Замечания валидации"));
    build.validation_issues.forEach((issue) => issues.append(node("p", issue.blocking ? "error-copy" : "form-note", `${issue.code}: ${issue.message}`)));
    body.append(issues);
  }
  const reason = node("textarea"); reason.placeholder = "Причина отклонения (обязательна только при reject)"; reason.maxLength = 2000;
  body.append(formField("Комментарий QA", reason));
  const actions = node("div", "review-actions");
  actions.append(actionButton("Отклонить сборку", "button-secondary", () => submitQa(item, build, false, reason.value)));
  actions.append(actionButton("Утвердить сборку", "button-primary", () => submitQa(item, build, true, reason.value)));
  body.append(actions);
}

async function submitQa(item, build, approved, reason) {
  try {
    await api(`/api/v1/packages/${item.id}/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ package_build_id: build.id, approved, reviewer: "operator", reason: reason.trim() || null })
    });
    $("#review-dialog").close(); toast(approved ? "Сборка прошла QA" : "Сборка отклонена"); await loadPackages();
  } catch (error) { toast(error.message, true); }
}

async function openRelease(item) {
  const dialog = $("#review-dialog");
  $("#review-title").textContent = appState.assets.get(item.source_asset_id)?.original_name || "Экспорт и доставка";
  $("#review-body").replaceChildren(node("div", "review-empty", "Загрузка результата…"));
  dialog.showModal();
  await loadRelease(item);
}

async function loadRelease(item) {
  try {
    const [builds, reviews, deliveries] = await Promise.all([
      api(`/api/v1/packages/${item.id}/builds`),
      api(`/api/v1/packages/${item.id}/qa-reviews`),
      api(`/api/v1/deliveries?package_id=${encodeURIComponent(item.id)}`)
    ]);
    const approvedReview = [...reviews].reverse().find((value) => value.decision === "approved");
    const build = approvedReview ? builds.find((value) => value.id === approvedReview.package_build_id) : builds.at(-1);
    if (!build) throw new Error("Утверждённая сборка не найдена");
    const exports = await api(`/api/v1/package-builds/${build.id}/exports`);
    renderRelease(item, build, exports, deliveries);
  } catch (error) { $("#review-body").replaceChildren(node("div", "review-empty", error.message)); }
}

function renderRelease(item, build, exports, deliveries) {
  $("#review-kicker").textContent = `Результат · ${build.customer} · ${build.base_name}`;
  const body = $("#review-body"); body.replaceChildren();
  const intro = node("div", "review-intro");
  intro.append(node("p", "", "ZIP остаётся локальным артефактом. Доставка отправляет сначала master, затем sidecars и проверяет размеры и SHA-256."));
  body.append(intro);

  const exportSection = node("section", "qa-section"); exportSection.append(node("h3", "", "Локальный ZIP"));
  const completedExport = [...exports].reverse().find((value) => value.state === "succeeded");
  const activeExport = [...exports].reverse().find((value) => value.state === "running");
  if (completedExport) {
    const row = node("div", "release-row");
    row.append(node("span", "", `${formatBytes(completedExport.archive_size_bytes)} · SHA ${completedExport.archive_sha256.slice(0, 12)}…`));
    const link = node("a", "button button-secondary", "Скачать ZIP"); link.href = `/api/v1/exports/${completedExport.id}/download`; row.append(link); exportSection.append(row);
  } else if (activeExport) {
    exportSection.append(node("p", "muted", "ZIP создаётся фоновым заданием…"));
  } else {
    exportSection.append(actionButton("Создать ZIP", "button-secondary", () => createExport(item, build)));
  }
  body.append(exportSection);

  const deliverySection = node("section", "qa-section"); deliverySection.append(node("h3", "", "Доставка"));
  const delivery = deliveries.at(-1);
  if (delivery) {
    const status = node("div", "delivery-status");
    status.append(node("span", `badge ${tone(delivery.state)}`, delivery.state));
    status.append(node("span", "muted", `${delivery.provider} → ${delivery.destination}${delivery.prefix ? `/${delivery.prefix}` : ""}`));
    deliverySection.append(status);
    if (delivery.error_message) deliverySection.append(node("p", "error-copy", `${delivery.error_code}: ${delivery.error_message}`));
    if (delivery.state === "failed") deliverySection.append(actionButton("Повторить доставку", "button-primary", () => retryDelivery(item, delivery)));
    if (delivery.state === "complete") deliverySection.append(node("p", "form-note", `Комплект доставлен ${formatDate(delivery.package_complete_at)}. Удалённые файлы не перезаписываются и не удаляются.`));
  } else {
    const prefix = node("input"); prefix.placeholder = "Необязательный безопасный префикс";
    deliverySection.append(formField("Папка назначения", prefix));
    deliverySection.append(actionButton("Запустить доставку", "button-primary", () => createDelivery(item, build, prefix.value)));
  }
  body.append(deliverySection);
}

async function createExport(item, build) {
  try {
    await api(`/api/v1/package-builds/${build.id}/exports`, {
      method: "POST", headers: { "Idempotency-Key": `export-${build.id}-${crypto.randomUUID()}` }
    });
    toast("ZIP поставлен в очередь"); await loadRelease(item);
  } catch (error) { toast(error.message, true); }
}

async function createDelivery(item, build, prefix) {
  try {
    await api(`/api/v1/packages/${item.id}/deliver`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": `delivery-${build.id}-${crypto.randomUUID()}` },
      body: JSON.stringify({ package_build_id: build.id, prefix: prefix.trim() })
    });
    $("#review-dialog").close(); toast("Доставка поставлена в очередь"); await loadPackages();
  } catch (error) { toast(error.message, true); }
}

async function retryDelivery(item, delivery) {
  try {
    await api(`/api/v1/deliveries/${delivery.id}/retry`, {
      method: "POST", headers: { "Idempotency-Key": `retry-${delivery.id}-${crypto.randomUUID()}` }
    });
    $("#review-dialog").close(); toast("Повторная доставка поставлена в очередь"); await loadPackages();
  } catch (error) { toast(error.message, true); }
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
  const keyInput = $("#api-key"); keyInput.value = appState.apiKey;
  keyInput.addEventListener("change", async () => {
    appState.apiKey = keyInput.value.trim();
    if (appState.apiKey) sessionStorage.setItem("mediaFactoryApiKey", appState.apiKey);
    else sessionStorage.removeItem("mediaFactoryApiKey");
    await loadPrincipal(); await loadPackages();
  });
  $("#review-close").addEventListener("click", () => $("#review-dialog").close());
  $("#review-dialog").addEventListener("click", (event) => { if (event.target === $("#review-dialog")) $("#review-dialog").close(); });
}

bindEvents();
loadHealth();
loadMetrics();
loadPrincipal();
loadPackages({ preserveSelection: false });
setInterval(loadHealth, 15000);
setInterval(loadMetrics, 15000);
setInterval(() => loadPackages(), 10000);
