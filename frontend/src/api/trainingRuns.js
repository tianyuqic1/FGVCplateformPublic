const DEFAULT_TIMEOUT_MS = 2500;
const CREATE_TRAINING_TIMEOUT_MS = 15000;

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
      const message = errorMessageFromDetail(payload?.detail);
      detail = message ? `${detail}: ${message}` : detail;
    } catch {
      // Keep the HTTP status fallback when the response body is not JSON.
    }
    throw new Error(detail);
  }

  if (response.status === 204) return null;
  return response.json();
}

function withTimeout(request, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  return request(controller.signal).finally(() => window.clearTimeout(timer));
}

function normalizeStatus(value) {
  if (["queued", "paused", "running", "succeeded", "failed", "cancelled"].includes(value)) return value;
  if (value === "done") return "succeeded";
  return "queued";
}

function progressForStatus(status) {
  if (status === "succeeded") return 100;
  if (status === "running") return 72;
  if (status === "paused") return 12;
  if (status === "failed" || status === "cancelled") return 100;
  return 8;
}

function normalizeTrainingProgress(metrics = {}, status = "queued") {
  const progress = metrics?.training_progress ?? metrics?.trainingProgress ?? null;
  if (progress && Array.isArray(progress.stages)) {
    return {
      currentStage: progress.current_stage ?? progress.currentStage ?? null,
      overallPercent: Number.isFinite(Number(progress.overall_percent))
        ? Number(progress.overall_percent)
        : progressForStatus(status),
      updatedAt: progress.updated_at ?? progress.updatedAt ?? null,
      stages: progress.stages.map((stage) => ({
        id: stage?.id ?? "stage",
        label: stage?.label ?? stage?.id ?? "stage",
        status: stage?.status ?? "pending",
        percent: Number.isFinite(Number(stage?.percent)) ? Number(stage.percent) : 0,
        weight: Number.isFinite(Number(stage?.weight)) ? Number(stage.weight) : null,
        note: stage?.note ?? null,
      })),
    };
  }
  return {
    currentStage: status,
    overallPercent: progressForStatus(status),
    updatedAt: null,
    stages: [],
  };
}

function metricLabel(metrics = {}, status = "queued") {
  if (Number.isFinite(Number(metrics.accuracy))) {
    return `acc ${(Number(metrics.accuracy) * 100).toFixed(1)}%`;
  }
  if (status === "failed") return "failed";
  if (status === "running") return "training";
  if (status === "paused") return "paused";
  return "queued";
}

export function extractTrainingRunList(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.training_runs)) return payload.training_runs;
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

export function extractTrainingRun(payload) {
  return payload?.training_run ?? payload?.data ?? payload;
}

export function normalizeTrainingRun(raw) {
  const status = normalizeStatus(raw?.status);
  const metrics = raw?.metrics ?? {};
  const id = raw?.id ?? raw?.run_id ?? raw?.model_version_id ?? raw?.job_id ?? "untracked-training-run";
  const datasetVersionId = raw?.datasetVersionId ?? raw?.dataset_version_id ?? null;
  const trainingProgress = normalizeTrainingProgress(metrics, status);
  const extractorConfig = raw?.extractorConfig ?? raw?.extractor_config ?? {};
  const headConfig = raw?.headConfig ?? raw?.head_config ?? {};

  return {
    id,
    name: raw?.name ?? `${datasetVersionId ?? "数据集"} 候选训练`,
    datasetId: raw?.datasetId ?? raw?.dataset_id ?? null,
    datasetVersionId,
    datasetName: raw?.datasetName ?? raw?.dataset_name ?? raw?.dataset_id ?? "数据集版本",
    modelVersionId: raw?.modelVersionId ?? raw?.model_version_id ?? null,
    featureArtifactId: raw?.featureArtifactId ?? raw?.feature_artifact_id ?? null,
    modelArtifactId: raw?.modelArtifactId ?? raw?.model_artifact_id ?? null,
    reportArtifactId: raw?.reportArtifactId ?? raw?.report_artifact_id ?? null,
    calibrationArtifactId: raw?.calibrationArtifactId ?? raw?.calibration_artifact_id ?? null,
    thresholdStrategyArtifactId: raw?.thresholdStrategyArtifactId ?? raw?.threshold_strategy_artifact_id ?? null,
    backboneId: raw?.backboneId ?? raw?.backbone_id ?? extractorConfig?.backbone_id ?? null,
    extractorConfig,
    headConfig,
    featurePool: raw?.featurePool ?? raw?.feature_pool ?? extractorConfig?.feature_pool ?? null,
    imageSize: raw?.imageSize ?? raw?.image_size ?? extractorConfig?.image_size ?? null,
    featureBatchSize:
      raw?.featureBatchSize ??
      raw?.feature_batch_size ??
      extractorConfig?.runtime?.feature_batch_size ??
      null,
    jobId: raw?.jobId ?? raw?.job_id ?? null,
    status,
    progress: Number.isFinite(Number(raw?.progress)) ? Number(raw.progress) : trainingProgress.overallPercent,
    trainingProgress,
    metric: raw?.metric ?? metricLabel(metrics, status),
    metrics,
    error: raw?.error ?? null,
    createdAt: raw?.createdAt ?? raw?.created_at ?? null,
    updatedAt: raw?.updatedAt ?? raw?.updated_at ?? null,
    startedAt: raw?.startedAt ?? raw?.started_at ?? null,
    finishedAt: raw?.finishedAt ?? raw?.finished_at ?? null,
  };
}

export async function listTrainingRuns() {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/training-runs", { signal });
    return extractTrainingRunList(payload).map(normalizeTrainingRun);
  });
}

export async function getTrainingRun(runId) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/training-runs/${encodeURIComponent(runId)}`, { signal });
    return normalizeTrainingRun(extractTrainingRun(payload));
  });
}

export async function createTrainingRun(input) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/training-runs", { method: "POST", body: input, signal });
    return normalizeTrainingRun(extractTrainingRun(payload));
  }, CREATE_TRAINING_TIMEOUT_MS);
}

export async function pauseTrainingRun(runId) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/training-runs/${encodeURIComponent(runId)}/pause`, { method: "POST", signal });
    return normalizeTrainingRun(extractTrainingRun(payload));
  });
}

export async function resumeTrainingRun(runId) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/training-runs/${encodeURIComponent(runId)}/resume`, { method: "POST", signal });
    return normalizeTrainingRun(extractTrainingRun(payload));
  });
}

export async function cancelTrainingRun(runId) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/training-runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", signal });
    return normalizeTrainingRun(extractTrainingRun(payload));
  });
}

export async function deleteTrainingRun(runId) {
  return withTimeout(async (signal) => {
    await fetchJson(`/api/training-runs/${encodeURIComponent(runId)}`, { method: "DELETE", signal });
    return true;
  });
}

function errorMessageFromDetail(detail) {
  if (typeof detail === "string") return detail;
  if (typeof detail?.message === "string") return detail.message;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = Array.isArray(item?.loc) ? item.loc.join(".") : "field";
        return item?.msg ? `${field}: ${item.msg}` : null;
      })
      .filter(Boolean)
      .join("; ");
  }
  return null;
}
