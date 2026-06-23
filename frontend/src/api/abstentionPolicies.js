const DEFAULT_TIMEOUT_MS = 15000;

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

function normalizePolicy(raw = {}) {
  const metrics = raw.metrics ?? {};
  return {
    id: raw.policyId ?? raw.policy_id ?? "policy-preview",
    datasetId: raw.datasetId ?? raw.dataset_id ?? null,
    datasetVersionId: raw.datasetVersionId ?? raw.dataset_version_id ?? null,
    modelVersionId: raw.modelVersionId ?? raw.model_version_id ?? null,
    status: raw.status ?? "shadow",
    targetSelectiveRisk: Number(raw.targetSelectiveRisk ?? raw.target_selective_risk ?? 0),
    tauConf: Number(raw.tauConf ?? raw.tau_conf ?? 0),
    tauMargin: Number(raw.tauMargin ?? raw.tau_margin ?? 0),
    tauOod: raw.tauOod ?? raw.tau_ood ?? null,
    sourceFeedbackCount: Number(raw.sourceFeedbackCount ?? raw.source_feedback_count ?? 0),
    estimatedCoverage: Number(raw.estimatedCoverage ?? raw.estimated_coverage ?? metrics.coverage ?? 0),
    estimatedSelectiveRisk: Number(raw.estimatedSelectiveRisk ?? raw.estimated_selective_risk ?? metrics.selective_risk ?? 0),
    estimatedReviewCost: Number(raw.estimatedReviewCost ?? raw.estimated_review_cost ?? metrics.estimated_review_cost ?? 0),
    metrics,
    selectionConfig: raw.selectionConfig ?? raw.selection_config ?? {},
    createdBy: raw.createdBy ?? raw.created_by ?? null,
    createdAt: raw.createdAt ?? raw.created_at ?? null,
  };
}

function normalizeShadowDecision(raw = {}) {
  return {
    id: raw.shadowDecisionId ?? raw.shadow_decision_id ?? "shadow-preview",
    policyId: raw.policyId ?? raw.policy_id ?? null,
    inferenceEventId: raw.inferenceEventId ?? raw.inference_event_id ?? null,
    datasetId: raw.datasetId ?? raw.dataset_id ?? null,
    datasetVersionId: raw.datasetVersionId ?? raw.dataset_version_id ?? null,
    modelVersionId: raw.modelVersionId ?? raw.model_version_id ?? null,
    currentDecision: raw.currentDecision ?? raw.current_decision ?? "abstain",
    shadowDecision: raw.shadowDecision ?? raw.shadow_decision ?? "abstain",
    decisionDiff: raw.decisionDiff ?? raw.decision_diff ?? "same",
    scoreSnapshot: raw.scoreSnapshot ?? raw.score_snapshot ?? {},
    createdAt: raw.createdAt ?? raw.created_at ?? null,
  };
}

export async function proposeAbstentionPolicy(input) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/abstention-policies/propose", {
      method: "POST",
      body: input,
      signal,
    });
    return normalizePolicy(payload.policy);
  });
}

export async function listAbstentionPolicies({ datasetVersionId, modelVersionId, status = "all", limit = 20 } = {}) {
  const params = new URLSearchParams();
  if (datasetVersionId) params.set("dataset_version_id", datasetVersionId);
  if (modelVersionId) params.set("model_version_id", modelVersionId);
  if (status) params.set("status", status);
  if (limit) params.set("limit", String(limit));
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/abstention-policies?${params.toString()}`, { signal });
    return (payload.policies ?? []).map(normalizePolicy);
  });
}

export async function listAbstentionShadowDecisions(policyId, { diff = "all", limit = 50 } = {}) {
  const params = new URLSearchParams();
  if (diff) params.set("diff", diff);
  if (limit) params.set("limit", String(limit));
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/abstention-policies/${encodeURIComponent(policyId)}/shadow-decisions?${params.toString()}`, { signal });
    return (payload.shadow_decisions ?? []).map(normalizeShadowDecision);
  });
}
