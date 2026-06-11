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
    throw new Error(`${response.status} ${method} ${path}`);
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

export function extractJobList(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.jobs)) return payload.jobs;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.data)) return payload.data;
  return [];
}

export function extractJob(payload) {
  return payload?.job ?? payload?.data ?? payload;
}

export function normalizeJobStatus(value) {
  if (["queued", "running", "succeeded", "failed", "cancelled"].includes(value)) return value;
  if (["pending", "created", "scheduled", "waiting"].includes(value)) return "queued";
  if (["active", "started", "in_progress", "processing"].includes(value)) return "running";
  if (["done", "success", "completed", "complete", "passed"].includes(value)) return "succeeded";
  if (["cancelled", "canceled"].includes(value)) return "cancelled";
  if (["error", "errored", "timeout"].includes(value)) return "failed";
  return "queued";
}

export function normalizeJob(raw) {
  const jobType = raw?.job_type ?? raw?.type ?? raw?.kind ?? raw?.task_type ?? "unknown";
  const datasetVersionId =
    raw?.datasetVersionId ??
    raw?.dataset_version_id ??
    raw?.input?.dataset_version_id ??
    raw?.payload?.dataset_version_id ??
    null;
  const datasetId = raw?.datasetId ?? raw?.dataset_id ?? raw?.input?.dataset_id ?? raw?.payload?.dataset_id ?? null;

  return {
    id: raw?.id ?? raw?.job_id ?? raw?.run_id ?? `${jobType}-job`,
    jobType,
    status: normalizeJobStatus(raw?.status ?? raw?.state),
    datasetId,
    datasetVersionId,
    progress: Math.max(0, Math.min(100, firstNumber(raw?.progress, raw?.progress_percent, raw?.percent, 0))),
    createdAt: raw?.createdAt ?? raw?.created_at ?? raw?.submitted_at ?? null,
    updatedAt: raw?.updatedAt ?? raw?.updated_at ?? raw?.finished_at ?? null,
    message: raw?.message ?? raw?.status_message ?? raw?.error ?? "",
  };
}

export async function listJobs() {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/jobs", { signal });
    return extractJobList(payload).map(normalizeJob);
  });
}

export async function getJob(jobId) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/jobs/${encodeURIComponent(jobId)}`, { signal });
    return normalizeJob(extractJob(payload));
  });
}

export async function createJob(input) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/jobs", { method: "POST", body: input, signal });
    return normalizeJob(extractJob(payload));
  });
}
