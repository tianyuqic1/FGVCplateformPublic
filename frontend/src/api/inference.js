const DEFAULT_TIMEOUT_MS = 10000;

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
    throw new Error(`${response.status} ${method} ${path}`);
  }

  return response.json();
}

function withTimeout(request, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  return request(controller.signal).finally(() => window.clearTimeout(timer));
}

export function extractInferenceResult(payload) {
  return payload?.inference_result ?? payload?.result ?? payload;
}

function normalizeCandidate(raw) {
  return {
    label: raw?.label ?? "unknown",
    score: Number.isFinite(Number(raw?.score)) ? Number(raw.score) : 0,
  };
}

function normalizeNeighbor(raw) {
  return {
    sampleId: raw?.sampleId ?? raw?.sample_id ?? null,
    label: raw?.label ?? "unknown",
    distance: Number.isFinite(Number(raw?.distance)) ? Number(raw.distance) : null,
  };
}

function normalizeDecision(raw = {}) {
  return {
    value: raw?.value ?? raw?.decision ?? "abstain",
    reasons: Array.isArray(raw?.reasons) ? raw.reasons : [],
    thresholds: raw?.thresholds ?? {},
    confidence: Number.isFinite(Number(raw?.confidence)) ? Number(raw.confidence) : 0,
    margin: Number.isFinite(Number(raw?.margin)) ? Number(raw.margin) : 0,
    oodScore: Number.isFinite(Number(raw?.oodScore ?? raw?.ood_score)) ? Number(raw?.oodScore ?? raw?.ood_score) : null,
  };
}

export function normalizeInferenceResult(raw) {
  const result = raw?.result ?? raw;
  return {
    datasetId: raw?.datasetId ?? raw?.dataset_id ?? result?.dataset_id ?? null,
    datasetVersionId: raw?.datasetVersionId ?? raw?.dataset_version_id ?? result?.dataset_version_id ?? null,
    modelVersionId: raw?.modelVersionId ?? raw?.model_version_id ?? null,
    modelArtifactId: raw?.modelArtifactId ?? raw?.model_artifact_id ?? result?.model_artifact_id ?? null,
    featureArtifactId: raw?.featureArtifactId ?? raw?.feature_artifact_id ?? null,
    thresholdStrategyId:
      raw?.thresholdStrategyId ?? raw?.threshold_strategy_id ?? result?.threshold_strategy_id ?? null,
    input: raw?.input ?? {},
    topK: (result?.topK ?? result?.top_k ?? []).map(normalizeCandidate),
    decision: normalizeDecision(result?.decision),
    nearestNeighbors: (result?.nearestNeighbors ?? result?.nearest_neighbors ?? []).map(normalizeNeighbor),
  };
}

export async function runInference(input) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/inference", { method: "POST", body: input, signal });
    return normalizeInferenceResult(extractInferenceResult(payload));
  });
}
