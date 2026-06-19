const DEFAULT_TIMEOUT_MS = 120000;

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

async function fetchForm(path, formData, { method = "POST", signal } = {}) {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    method,
    headers: {
      Accept: "application/json",
    },
    body: formData,
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

export async function runInferenceUpload(input) {
  const formData = new FormData();
  formData.append("dataset_version_id", input.dataset_version_id);
  formData.append("model_version_id", input.model_version_id);
  formData.append("image", input.image);
  formData.append("top_k", String(input.top_k ?? 3));
  formData.append("evidence_k", String(input.evidence_k ?? 3));
  if (input.accept_threshold != null) formData.append("accept_threshold", String(input.accept_threshold));
  if (input.margin_threshold != null) formData.append("margin_threshold", String(input.margin_threshold));
  if (input.ood_distance_threshold != null) {
    formData.append("ood_distance_threshold", String(input.ood_distance_threshold));
  }

  return withTimeout(async (signal) => {
    const payload = await fetchForm("/api/inference/upload", formData, { signal });
    return normalizeInferenceResult(extractInferenceResult(payload));
  });
}
