import { APIError, fetchJson } from "../src/api/http.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
const originalCustomEvent = globalThis.CustomEvent;
let diagnosticEvent = null;
globalThis.window = { dispatchEvent: (event) => { diagnosticEvent = event; } };
if (typeof globalThis.CustomEvent !== "function") {
  globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) { this.type = type; this.detail = options.detail; }
  };
}
globalThis.fetch = async () => new Response(
  JSON.stringify({ error: { code: "BROKEN", message: "训练服务不可用", request_id: "request-from-body" } }),
  { status: 503, headers: { "Content-Type": "application/json", "X-Request-ID": "request-from-header" } },
);

try {
  await fetchJson("/api/test");
  throw new Error("expected fetchJson to reject");
} catch (error) {
  assert(error instanceof APIError, "uses the stable APIError contract");
  assert(error.status === 503, "preserves HTTP status");
  assert(error.code === "BROKEN", "preserves stable error code");
  assert(error.requestId === "request-from-body", "prefers the signed response envelope request id");
  assert(error.message.includes("诊断编号 request-from-body"), "shows a copyable diagnostic id");
  assert(diagnosticEvent?.type === "finevision:diagnostic-error", "publishes a global diagnostic event");
  assert(diagnosticEvent?.detail?.requestId === "request-from-body", "publishes the request id for the copy action");
} finally {
  globalThis.fetch = originalFetch;
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
  if (originalCustomEvent === undefined) delete globalThis.CustomEvent;
  else globalThis.CustomEvent = originalCustomEvent;
}

console.log("diagnostics client smoke passed");
