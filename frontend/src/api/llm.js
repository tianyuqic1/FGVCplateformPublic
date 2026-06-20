const DEFAULT_TIMEOUT_MS = 90000;

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
      // Keep status fallback when the response body is not JSON.
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

export function normalizeAssistance(raw = {}) {
  return {
    task: raw?.task ?? "unknown",
    advisoryOnly: raw?.advisoryOnly ?? raw?.advisory_only ?? true,
    provider: raw?.provider ?? null,
    model: raw?.model ?? null,
    reasoningEffort: raw?.reasoningEffort ?? raw?.reasoning_effort ?? null,
    createdAt: raw?.createdAt ?? raw?.created_at ?? null,
    summary: raw?.summary ?? "",
    inspectionNotes: raw?.inspectionNotes ?? raw?.inspection_notes ?? [],
    suggestedActions: raw?.suggestedActions ?? raw?.suggested_actions ?? [],
    riskFlags: raw?.riskFlags ?? raw?.risk_flags ?? [],
    confidence: raw?.confidence ?? "unknown",
  };
}

export async function generateReviewAssistance(reviewItemId, input = {}) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson(`/api/review-items/${encodeURIComponent(reviewItemId)}/assist`, {
      method: "POST",
      body: input,
      signal,
    });
    return {
      assistance: normalizeAssistance(payload?.assistance),
      reviewItem: payload?.review_item ?? null,
    };
  });
}

export async function generateLLMAssistance({ task, context }) {
  return withTimeout(async (signal) => {
    const payload = await fetchJson("/api/llm/assist", {
      method: "POST",
      body: { task, context },
      signal,
    });
    return normalizeAssistance(payload?.assistance);
  });
}
