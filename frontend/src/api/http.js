const DEFAULT_TIMEOUT_MS = 2500;

export class APIError extends Error {
  constructor(message, { status = 0, code = "HTTP_ERROR", requestId = "", method = "GET", path = "", payload = null } = {}) {
    const diagnostic = requestId ? `${message}（诊断编号 ${requestId}）` : message;
    super(diagnostic);
    this.name = "APIError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.method = method;
    this.path = path;
    this.payload = payload;
    publishDiagnosticError(this);
  }
}

function publishDiagnosticError(error) {
  if (!error.requestId || typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  if (typeof CustomEvent !== "function") return;
  window.dispatchEvent(new CustomEvent("finevision:diagnostic-error", {
    detail: {
      message: error.message,
      requestId: error.requestId,
      status: error.status,
      method: error.method,
      path: error.path,
    },
  }));
}

export function apiBaseUrl() {
  const configured = import.meta.env?.VITE_API_BASE_URL;
  return configured ? configured.replace(/\/$/, "") : "";
}

export async function fetchJson(path, { method = "GET", body, signal } = {}) {
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
    throw await apiErrorFromResponse(response, { method, path });
  }
  if (response.status === 204) return null;
  return response.json();
}

export async function fetchForm(path, formData, { method = "POST", signal, fallback = "" } = {}) {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    method,
    headers: { Accept: "application/json" },
    body: formData,
    signal,
  });
  if (!response.ok) throw await apiErrorFromResponse(response, { method, path, fallback });
  if (response.status === 204) return null;
  return response.json();
}

export async function apiErrorFromResponse(response, { method = "GET", path = "", fallback = "" } = {}) {
  let detail = fallback || `${response.status} ${method} ${path}`;
  let code = "HTTP_ERROR";
  let bodyRequestId = "";
  let payload = null;
  try {
    payload = await response.json();
    const envelope = payload?.error ?? payload?.detail;
    const message = errorMessage(envelope);
    detail = message ? `${detail}: ${message}` : detail;
    if (typeof envelope?.code === "string") code = envelope.code;
    if (typeof envelope?.request_id === "string") bodyRequestId = envelope.request_id;
  } catch {
    // Preserve the status fallback when the response is not JSON.
  }
  const requestId = bodyRequestId || response.headers.get("X-Request-ID") || "";
  return new APIError(detail, { status: response.status, code, requestId, method, path, payload });
}

export function withTimeout(request, timeoutMs = DEFAULT_TIMEOUT_MS, externalSignal) {
  const controller = new AbortController();
  let timedOut = false;
  const onAbort = () => controller.abort(externalSignal.reason);
  if (externalSignal?.aborted) onAbort();
  else externalSignal?.addEventListener("abort", onAbort, { once: true });
  const timer = window.setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  return Promise.resolve().then(() => request(controller.signal)).catch((error) => {
    if (timedOut) {
      const timeoutError = new Error(`请求超过 ${Math.ceil(timeoutMs / 1000)} 秒，已停止等待`);
      timeoutError.name = "TimeoutError";
      throw timeoutError;
    }
    throw error;
  }).finally(() => {
    window.clearTimeout(timer);
    externalSignal?.removeEventListener("abort", onAbort);
  });
}

function errorMessage(detail) {
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
