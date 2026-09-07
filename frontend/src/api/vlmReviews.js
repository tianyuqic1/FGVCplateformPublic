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
      if (payload?.detail) detail = `${detail}: ${payload.detail}`;
    } catch {
      // Keep the HTTP status fallback.
    }
    throw new Error(detail);
  }
  return response.json();
}

function withTimeout(request, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  return request(controller.signal)
    .catch((error) => {
      if (error?.name === "AbortError") throw new Error("VLM 任务请求超时");
      throw error;
    })
    .finally(() => window.clearTimeout(timer));
}

export function normalizeVLMReviewRun(raw = {}) {
  return {
    id: raw?.runId ?? raw?.run_id ?? null,
    datasetId: raw?.datasetId ?? raw?.dataset_id ?? null,
    inferenceRunId: raw?.inferenceRunId ?? raw?.inference_run_id ?? null,
    mode: raw?.mode ?? "assisted",
    status: raw?.status ?? "queued",
    totalCount: Number(raw?.totalCount ?? raw?.total_count ?? 0),
    succeededCount: Number(raw?.succeededCount ?? raw?.succeeded_count ?? 0),
    failedCount: Number(raw?.failedCount ?? raw?.failed_count ?? 0),
    skippedCount: Number(raw?.skippedCount ?? raw?.skipped_count ?? 0),
    fallbackCount: Number(raw?.fallbackCount ?? raw?.fallback_count ?? 0),
    modelId: raw?.modelId ?? raw?.model_id ?? "Fine-R1-3B",
    promptVersion: raw?.promptVersion ?? raw?.prompt_version ?? null,
    createdAt: raw?.createdAt ?? raw?.created_at ?? null,
  };
}

export async function listVLMReviewRuns({ limit = 10 } = {}) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/vlm-review-runs?limit=${limit}`, { signal });
    return (payload?.vlm_review_runs ?? []).map(normalizeVLMReviewRun);
  });
}

export async function getVLMReviewCapabilities() {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/vlm-review-capabilities", { signal });
    return {
      assistedEnabled: payload?.assisted_enabled !== false,
      autoEnabled: payload?.auto_enabled === true,
      autoEnablementPolicy: payload?.auto_enablement_policy ?? null,
      rejectOodAllowed: payload?.reject_ood_allowed === true,
    };
  });
}

export async function createVLMReviewRun(input) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/vlm-review-runs", {
      method: "POST",
      body: input,
      signal,
    });
    return normalizeVLMReviewRun(payload?.vlm_review_run);
  });
}

export async function cancelVLMReviewRun(runId) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/vlm-review-runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      signal,
    });
    return normalizeVLMReviewRun(payload?.vlm_review_run);
  });
}
