import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../src/api/vlmReviews.js", import.meta.url), "utf8");

for (const required of [
  "/api/vlm-review-runs",
  "createVLMReviewRun",
  "normalizeVLMReviewRun",
  "cancelVLMReviewRun",
  "getVLMReviewCapabilities",
]) {
  if (!source.includes(required)) throw new Error(`Missing VLM client contract: ${required}`);
}

const transformed = source
  .replace(/export function normalizeVLMReviewRun/, "function normalizeVLMReviewRun")
  .replace(/export async function listVLMReviewRuns/, "async function listVLMReviewRuns")
  .replace(/export async function getVLMReviewCapabilities/, "async function getVLMReviewCapabilities")
  .replace(/export async function createVLMReviewRun/, "async function createVLMReviewRun")
  .replace(/export async function cancelVLMReviewRun/, "async function cancelVLMReviewRun")
  .concat("\nglobalThis.__normalize = normalizeVLMReviewRun;");

const context = vm.createContext({
  import: { meta: { env: {} } },
  URLSearchParams,
  window: { setTimeout, clearTimeout },
});
try {
  vm.runInContext(transformed.replaceAll("import.meta.env", "({})"), context);
} catch (error) {
  throw new Error(`VLM client is not parseable: ${error.message}`);
}

const normalized = context.__normalize({
  run_id: "vlm-run-1",
  total_count: 20,
  succeeded_count: 7,
  fallback_count: 2,
});
if (normalized.id !== "vlm-run-1" || normalized.totalCount !== 20 || normalized.fallbackCount !== 2) {
  throw new Error("VLM run normalization failed");
}

console.log("VLM review client smoke passed.");
