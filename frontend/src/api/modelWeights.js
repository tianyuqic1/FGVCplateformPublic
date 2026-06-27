const DEFAULT_TIMEOUT_MS = 2500;

function apiBaseUrl() {
  const configured = import.meta.env?.VITE_API_BASE_URL;
  return configured ? configured.replace(/\/$/, "") : "";
}

async function fetchJson(path, { method = "GET", signal } = {}) {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    method,
    headers: { Accept: "application/json" },
    signal,
  });

  if (!response.ok) {
    throw new Error(`${response.status} ${method} ${path}`);
  }
  return response.json();
}

function withTimeout(request, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  return request(controller.signal).finally(() => window.clearTimeout(timer));
}

function normalizeWeight(raw) {
  return {
    extractor: raw?.extractor ?? raw?.preset ?? "",
    backboneId: raw?.backbone_id ?? raw?.backboneId ?? "",
    modelName: raw?.model_name ?? raw?.modelName ?? "",
    state: raw?.state ?? raw?.cache_status ?? raw?.cacheStatus ?? "missing",
    cacheBytes: Number(raw?.cache_bytes ?? raw?.cacheBytes ?? raw?.complete_size_bytes ?? 0),
    partialBytes: Number(raw?.partial_bytes ?? raw?.partialBytes ?? raw?.incomplete_size_bytes ?? 0),
    cacheDir: raw?.cache_dir ?? raw?.cacheDir ?? "",
    repoId: raw?.repo_id ?? raw?.repoId ?? "",
    completeFileCount: Number(raw?.complete_file_count ?? raw?.completeFileCount ?? 0),
    incompleteFileCount: Number(raw?.incomplete_file_count ?? raw?.incompleteFileCount ?? 0),
    description: raw?.description ?? "",
    downloadHint: raw?.download_hint ?? raw?.downloadHint ?? "timm 会通过 Hugging Face Hub 拉取预训练权重。",
  };
}

export async function listModelWeights() {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/model-weights", { signal });
    return Array.isArray(payload?.weights) ? payload.weights.map(normalizeWeight) : [];
  });
}

export async function deleteModelWeight(preset) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/model-weights/${encodeURIComponent(preset)}`, { method: "DELETE", signal });
    return {
      deleted: Boolean(payload?.deleted),
      preset: payload?.preset ?? preset,
      cacheDir: payload?.cache_dir ?? payload?.cacheDir ?? "",
      before: payload?.before ? normalizeWeight(payload.before) : null,
      after: payload?.after ? normalizeWeight(payload.after) : null,
    };
  }, 10000);
}
