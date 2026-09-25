import { fetchForm, fetchJson, withTimeout as withHttpTimeout } from "./http.js";

const DEFAULT_TIMEOUT_MS = 120000;

function withTimeout(request, timeoutMs = DEFAULT_TIMEOUT_MS) {
  return withHttpTimeout(request, timeoutMs);
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
    value: raw?.value ?? raw?.decision ?? "unknown",
    reasons: Array.isArray(raw?.reasons) ? raw.reasons : [],
    thresholds: raw?.thresholds ?? {},
    confidence: Number.isFinite(Number(raw?.confidence)) ? Number(raw.confidence) : 0,
    margin: Number.isFinite(Number(raw?.margin)) ? Number(raw.margin) : 0,
    oodScore: Number.isFinite(Number(raw?.oodScore ?? raw?.ood_score)) ? Number(raw?.oodScore ?? raw?.ood_score) : null,
  };
}

export function normalizeInferenceResult(raw) {
  const result = raw?.result ?? raw;
  const inferenceRunId = raw?.inferenceRunId ?? raw?.inference_run_id ?? raw?.batchId ?? raw?.batch_id ?? null;
  return {
    inferenceEventId: raw?.inferenceEventId ?? raw?.inference_event_id ?? null,
    inferenceRunId,
    batchId: raw?.batchId ?? raw?.batch_id ?? inferenceRunId,
    reviewItemId: raw?.reviewItemId ?? raw?.review_item_id ?? null,
    datasetId: raw?.datasetId ?? raw?.dataset_id ?? result?.dataset_id ?? null,
    datasetVersionId: raw?.datasetVersionId ?? raw?.dataset_version_id ?? result?.dataset_version_id ?? null,
    modelVersionId: raw?.modelVersionId ?? raw?.model_version_id ?? null,
    modelArtifactId: raw?.modelArtifactId ?? raw?.model_artifact_id ?? result?.model_artifact_id ?? null,
    runtime: raw?.runtime ?? result?.runtime ?? null,
    deploymentId: raw?.deployment_id ?? result?.deployment_id ?? null,
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
  if (input.deployment_id) formData.append("deployment_id", input.deployment_id);
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

export async function runInferenceUploadFolder(input) {
  const formData = new FormData();
  formData.append("dataset_version_id", input.dataset_version_id);
  formData.append("model_version_id", input.model_version_id);
  input.images.forEach((image) => {
    formData.append("images", image, image.webkitRelativePath || image.name);
  });
  if (input.deployment_id) formData.append("deployment_id", input.deployment_id);
  formData.append("top_k", String(input.top_k ?? 3));
  formData.append("evidence_k", String(input.evidence_k ?? 3));
  formData.append("route_all_to_review", String(input.route_all_to_review ?? true));
  if (input.accept_threshold != null) formData.append("accept_threshold", String(input.accept_threshold));
  if (input.margin_threshold != null) formData.append("margin_threshold", String(input.margin_threshold));
  if (input.ood_distance_threshold != null) {
    formData.append("ood_distance_threshold", String(input.ood_distance_threshold));
  }

  return withTimeout(async (signal) => {
    const payload = await fetchForm("/api/inference/upload-folder", formData, { signal });
    const batch = normalizeInferenceBatch(payload?.batch);
    return {
      batch,
      results: (payload?.results ?? []).map((item) => normalizeInferenceResult(extractInferenceResult(item))),
      failures: payload?.failures ?? [],
    };
  }, 600000);
}

function normalizeInferenceBatch(raw = {}) {
  const inferenceRunId = raw?.inferenceRunId ?? raw?.inference_run_id ?? raw?.batchInferenceId ?? raw?.batch_inference_id ?? raw?.batchId ?? raw?.batch_id ?? null;
  return {
    ...raw,
    inference_run_id: inferenceRunId,
    batch_inference_id: raw?.batch_inference_id ?? inferenceRunId,
    batch_id: raw?.batch_id ?? inferenceRunId,
    total: Number.isFinite(Number(raw?.total)) ? Number(raw.total) : 0,
    succeeded: Number.isFinite(Number(raw?.succeeded)) ? Number(raw.succeeded) : 0,
    failed: Number.isFinite(Number(raw?.failed)) ? Number(raw.failed) : 0,
    review_item_count: Number.isFinite(Number(raw?.review_item_count)) ? Number(raw.review_item_count) : 0,
    review_item_ids: Array.isArray(raw?.review_item_ids) ? raw.review_item_ids : [],
    route_all_to_review: Boolean(raw?.route_all_to_review),
  };
}
