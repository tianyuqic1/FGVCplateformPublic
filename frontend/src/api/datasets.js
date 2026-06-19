const DEFAULT_TIMEOUT_MS = 2500;

function apiBaseUrl() {
  const configured = import.meta.env?.VITE_API_BASE_URL;
  return configured ? configured.replace(/\/$/, "") : "";
}

async function fetchJson(path, { method = "GET", body, signal } = {}) {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    method,
    headers: {
      Accept: "application/json",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });

  if (!response.ok) {
    let detail = `${response.status} ${method} ${path}`;
    try {
      const payload = await response.json();
      const message = typeof payload?.detail === "string" ? payload.detail : payload?.detail?.message;
      detail = message ? `${detail}: ${message}` : detail;
    } catch {
      // Keep the HTTP status fallback when the response body is not JSON.
    }
    throw new Error(detail);
  }

  return response.json();
}

function withTimeout(request, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  return request(controller.signal).finally(() => window.clearTimeout(timer));
}

function firstNumber(...values) {
  const value = values.find((item) => Number.isFinite(Number(item)));
  return value === undefined ? 0 : Number(value);
}

function normalizeStatus(value) {
  if (["production", "calibrating", "training"].includes(value)) return value;
  if (value === "ready") return "ready";
  if (["published", "active"].includes(value)) return "production";
  if (["needs_attention", "needs_calibration", "pending_calibration", "evaluating"].includes(value)) return "calibrating";
  return "training";
}

export function extractDatasetList(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.datasets)) return payload.datasets;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.data)) return payload.data;
  return [];
}

export function extractDataset(payload) {
  return payload?.dataset ?? payload?.data ?? payload;
}

export function extractImportedDataset(payload) {
  return {
    dataset: payload?.dataset ?? null,
    version: payload?.version ?? null,
  };
}

export function normalizeDataset(raw) {
  const classList = raw?.classes ?? raw?.class_names ?? raw?.taxonomy?.classes;
  const classCount = Array.isArray(classList) ? classList.length : firstNumber(raw?.classes, raw?.class_count, raw?.num_classes);
  const imageCount = firstNumber(raw?.images, raw?.image_count, raw?.sample_count, raw?.num_samples);
  const latestVersion =
    raw?.version ??
    raw?.datasetVersionId ??
    raw?.dataset_version_id ??
    raw?.latest_version_id ??
    raw?.current_version_id ??
    "dataset@draft";

  return {
    id: raw?.id ?? raw?.dataset_id ?? raw?.slug ?? latestVersion,
    name: raw?.name ?? raw?.display_name ?? raw?.dataset_id ?? "未命名数据集",
    datasetVersionId: raw?.datasetVersionId ?? raw?.dataset_version_id ?? latestVersion,
    classes: classCount,
    images: imageCount,
    version: latestVersion,
    modelVersion: raw?.modelVersion ?? raw?.model_version ?? raw?.production_model_version_id ?? "unreleased",
    productionModelVersionId: raw?.productionModelVersionId ?? raw?.production_model_version_id ?? null,
    featureArtifactId: raw?.featureArtifactId ?? raw?.feature_artifact_id ?? null,
    thresholdStrategyId: raw?.thresholdStrategyId ?? raw?.threshold_strategy_id ?? null,
    oodStressAssetId: raw?.oodStressAssetId ?? raw?.ood_stress_asset_id ?? null,
    status: normalizeStatus(raw?.status ?? raw?.readiness_status),
    quality: firstNumber(raw?.quality, raw?.quality_score, raw?.readiness?.quality, 0),
    coverage: firstNumber(raw?.coverage, raw?.expected_coverage, raw?.readiness?.coverage, 0),
    oodRecall: firstNumber(raw?.oodRecall, raw?.ood_recall, raw?.readiness?.ood_recall, 0),
    description: raw?.description ?? raw?.summary ?? "Control-plane API 已返回该数据集，详细描述待补充。",
  };
}

export async function listDatasets() {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/datasets", { signal });
    return extractDatasetList(payload).map(normalizeDataset);
  });
}

export async function getDataset(datasetId) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/datasets/${encodeURIComponent(datasetId)}`, { signal });
    return normalizeDataset(extractDataset(payload));
  });
}

export async function importImagefolder(input) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/datasets/import-imagefolder", { method: "POST", body: input, signal });
    return extractImportedDataset(payload);
  }, 10000);
}
