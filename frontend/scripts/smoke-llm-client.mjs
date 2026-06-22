import { generateLLMAssistance, generateReviewAssistance, normalizeAssistance } from "../src/api/llm.js";

const normalized = normalizeAssistance({
  task: "review_assistance",
  advisory_only: true,
  model: "gpt-5.5",
  created_at: "2026-06-20T00:00:00+00:00",
  summary: "Check the visual evidence before submitting.",
  holistic_analysis: "The image reference and dataset summary suggest a manual first-pass judgment is required.",
  inspection_notes: ["Compare top-k"],
  suggested_actions: ["Human reviewer chooses final label"],
  risk_flags: ["Advisory only"],
});

if (normalized.task !== "review_assistance") throw new Error("LLM task not normalized");
if (normalized.advisoryOnly !== true) throw new Error("Advisory flag not normalized");
if (!normalized.holisticAnalysis.includes("manual first-pass")) throw new Error("Holistic analysis missing");
if (normalized.inspectionNotes[0] !== "Compare top-k") throw new Error("Inspection notes missing");
if (normalized.suggestedActions[0] !== "Human reviewer chooses final label") throw new Error("Suggested actions missing");
if (normalized.riskFlags[0] !== "Advisory only") throw new Error("Risk flags missing");

let requestedUrl = "";
let requestedBody = null;
globalThis.window = {
  setTimeout,
  clearTimeout,
};
globalThis.fetch = async (url, options = {}) => {
  requestedUrl = url;
  requestedBody = options.body ? JSON.parse(options.body) : null;
  return {
    ok: true,
    json: async () => ({ assistance: normalized, review_item: { review_item_id: "review-001" } }),
  };
};

await generateReviewAssistance("review-001", { question: "what should I inspect?" });
if (!requestedUrl.includes("/api/review-items/review-001/assist")) throw new Error("Review assist endpoint missing");
if (requestedBody.question !== "what should I inspect?") throw new Error("Review assist question missing");

await generateLLMAssistance({ task: "training_diagnosis", context: { error: "failed" } });
if (!requestedUrl.includes("/api/llm/assist")) throw new Error("Generic LLM endpoint missing");
if (requestedBody.task !== "training_diagnosis") throw new Error("Generic LLM task missing");
if (requestedBody.context.error !== "failed") throw new Error("Generic LLM context missing");

console.log("llm api client smoke passed");
