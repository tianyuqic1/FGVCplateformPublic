import { apiBaseUrl, fetchJson, withTimeout as withHttpTimeout } from "./http.js";

const DEFAULT_TIMEOUT_MS = 10000;

function withTimeout(request, timeoutMs = DEFAULT_TIMEOUT_MS) {
  return withHttpTimeout(request, timeoutMs);
}

function toAssetUrl(value) {
  if (!value) return null;
  if (/^(https?:|blob:|data:)/.test(value)) return value;
  if (value.startsWith("/")) return `${apiBaseUrl()}${value}`;
  return value;
}

function imageUrlFromInputRef(inputRef) {
  if (!inputRef) return null;
  const normalized = String(inputRef).replace(/\\/g, "/");
  if (!normalized.includes("/uploads/")) return null;
  const filename = normalized.split("/").filter(Boolean).pop();
  return filename ? `/api/uploads/${filename}` : null;
}

export function extractReviewItemList(payload) {
  return payload?.review_items ?? payload?.items ?? [];
}

export function extractPagination(payload, fallbackLength = 0) {
  const pagination = payload?.pagination ?? {};
  const totalKnown = pagination.total !== null && pagination.total !== undefined && pagination.total !== "" && Number.isFinite(Number(pagination.total));
  const limit = Number.isFinite(Number(pagination.limit)) ? Number(pagination.limit) : fallbackLength;
  const offset = Number.isFinite(Number(pagination.offset)) ? Number(pagination.offset) : 0;
  const total = totalKnown ? Number(pagination.total) : fallbackLength;
  return {
    total,
    totalKnown,
    limit,
    offset,
    hasMore: Boolean(pagination.has_more ?? pagination.hasMore),
    nextOffset: Number.isFinite(Number(pagination.next_offset ?? pagination.nextOffset))
      ? Number(pagination.next_offset ?? pagination.nextOffset)
      : null,
  };
}

export function extractReviewItem(payload) {
  return payload?.review_item ?? payload?.item ?? payload;
}

export function extractFeedbackItemList(payload) {
  return payload?.feedback_items ?? payload?.items ?? [];
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

export function normalizeReviewItem(raw = {}) {
  const context = raw?.context ?? {};
  const decision = normalizeDecision(context?.decision);
  const topK = (context?.topK ?? context?.top_k ?? []).map(normalizeCandidate);
  const nearestNeighbors = (context?.nearestNeighbors ?? context?.nearest_neighbors ?? []).map(normalizeNeighbor);
  const inputRef = raw?.inputRef ?? raw?.input_ref ?? context?.input?.uploaded_image_path ?? context?.input?.image_path ?? null;
  const rawImageUrl =
    raw?.imageUrl ??
    raw?.image_url ??
    context?.input?.image_url ??
    context?.input?.uploaded_image_url ??
    imageUrlFromInputRef(inputRef);
  return {
    id: raw?.id ?? raw?.reviewItemId ?? raw?.review_item_id ?? null,
    inferenceEventId: raw?.inferenceEventId ?? raw?.inference_event_id ?? null,
    inferenceRunId: raw?.inferenceRunId ?? raw?.inference_run_id ?? raw?.batchId ?? raw?.batch_id ?? context?.inference_run_id ?? null,
    batchId: raw?.batchId ?? raw?.batch_id ?? raw?.inferenceRunId ?? raw?.inference_run_id ?? context?.inference_run_id ?? null,
    status: raw?.status ?? "pending",
    riskType: raw?.riskType ?? raw?.risk_type ?? "mixed",
    priority: Number.isFinite(Number(raw?.priority)) ? Number(raw.priority) : 100,
    reason: raw?.reason ?? "Needs human review",
    reasonCodes: raw?.reasonCodes ?? raw?.reason_codes ?? decision.reasons,
    datasetId: raw?.datasetId ?? raw?.dataset_id ?? context?.dataset_id ?? null,
    datasetVersionId: raw?.datasetVersionId ?? raw?.dataset_version_id ?? context?.dataset_version_id ?? null,
    modelVersionId: raw?.modelVersionId ?? raw?.model_version_id ?? context?.model_version_id ?? null,
    sampleId: raw?.sampleId ?? raw?.sample_id ?? context?.input?.sample_id ?? null,
    inputRef,
    imageUrl: toAssetUrl(rawImageUrl),
    topK,
    decision,
    nearestNeighbors,
    context,
    assistanceMetadata: raw?.assistanceMetadata ?? raw?.assistance_metadata ?? {},
    feedback: raw?.feedback ?? null,
    createdAt: raw?.createdAt ?? raw?.created_at ?? null,
    updatedAt: raw?.updatedAt ?? raw?.updated_at ?? null,
    feedbackedAt: raw?.feedbackedAt ?? raw?.feedbacked_at ?? null,
  };
}

export function normalizeFeedbackItem(raw = {}) {
  const inputRef = raw?.inputRef ?? raw?.input_ref ?? null;
  const rawImageUrl = raw?.imageUrl ?? raw?.image_url ?? imageUrlFromInputRef(inputRef);
  return {
    id: raw?.id ?? raw?.feedbackItemId ?? raw?.feedback_item_id ?? null,
    reviewItemId: raw?.reviewItemId ?? raw?.review_item_id ?? null,
    inferenceEventId: raw?.inferenceEventId ?? raw?.inference_event_id ?? null,
    inferenceRunId: raw?.inferenceRunId ?? raw?.inference_run_id ?? raw?.batchId ?? raw?.batch_id ?? null,
    batchId: raw?.batchId ?? raw?.batch_id ?? raw?.inferenceRunId ?? raw?.inference_run_id ?? null,
    datasetId: raw?.datasetId ?? raw?.dataset_id ?? null,
    datasetVersionId: raw?.datasetVersionId ?? raw?.dataset_version_id ?? null,
    modelVersionId: raw?.modelVersionId ?? raw?.model_version_id ?? null,
    sampleId: raw?.sampleId ?? raw?.sample_id ?? null,
    inputRef,
    imageUrl: toAssetUrl(rawImageUrl),
    finalOutcome: raw?.finalOutcome ?? raw?.final_outcome ?? "uncertain",
    destination: raw?.destination ?? "taxonomy_dispute",
    finalLabel: raw?.finalLabel ?? raw?.final_label ?? null,
    reviewerNote: raw?.reviewerNote ?? raw?.reviewer_note ?? null,
    createdBy: raw?.createdBy ?? raw?.created_by ?? null,
    createdAt: raw?.createdAt ?? raw?.created_at ?? null,
  };
}

export async function listReviewItemsPage({ status = "pending", datasetId, limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (datasetId) params.set("dataset_id", datasetId);
  if (limit) params.set("limit", String(limit));
  if (offset) params.set("offset", String(offset));
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/review-items?${params.toString()}`, { signal });
    const items = extractReviewItemList(payload).map(normalizeReviewItem);
    return {
      items,
      pagination: extractPagination(payload, items.length),
    };
  });
}

export async function listReviewItems(filters = {}) {
  const page = await listReviewItemsPage(filters);
  return page.items;
}

export async function listFeedbackItems({ destination = "all", datasetId, limit = 100 } = {}) {
  const params = new URLSearchParams();
  if (destination) params.set("destination", destination);
  if (datasetId) params.set("dataset_id", datasetId);
  if (limit) params.set("limit", String(limit));
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/feedback-items?${params.toString()}`, { signal });
    return extractFeedbackItemList(payload).map(normalizeFeedbackItem);
  });
}

export async function getReviewItem(reviewItemId) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/review-items/${encodeURIComponent(reviewItemId)}`, { signal });
    return normalizeReviewItem(extractReviewItem(payload));
  });
}

export async function submitReviewOutcome(reviewItemId, input) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/review-items/${encodeURIComponent(reviewItemId)}/submit`, {
      method: "POST",
      body: input,
      signal,
    });
    return {
      reviewItem: normalizeReviewItem(extractReviewItem(payload)),
      feedbackItem: payload?.feedback_item ?? null,
    };
  });
}
