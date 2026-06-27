import {
  extractInferenceResult,
  normalizeInferenceResult,
} from "../src/api/inference.js";

const payload = {
  inference_result: {
    dataset_id: "toy",
    dataset_version_id: "dataset@toy-001",
    model_version_id: "toy-run-001-candidate",
    model_artifact_id: "dataset@toy-001-run-001-linear-head",
    feature_artifact_id: "dataset@toy-001-color_stats_v1-cff1350237",
    threshold_strategy_id: "dataset@toy-001-run-001-threshold-strategy",
    inference_run_id: "infer-run-001",
    batch_id: "infer-run-001",
    inference_event_id: "inference-001",
    review_item_id: "review-001",
    input: { sample_id: "sample-001" },
    result: {
      top_k: [
        { label: "red_square", score: 0.92 },
        { label: "green_circle", score: 0.06 },
      ],
      decision: {
        decision: "accept",
        reasons: ["meets_acceptance_thresholds"],
        thresholds: { confidence: 0.8, margin: 0.15, ood_distance: 1.2 },
        confidence: 0.92,
        margin: 0.86,
        ood_score: 0.22,
      },
      nearest_neighbors: [
        { sample_id: "sample-002", label: "red_square", distance: 0.22 },
      ],
    },
  },
};

const result = normalizeInferenceResult(extractInferenceResult(payload));
if (result.datasetVersionId !== "dataset@toy-001") throw new Error("Dataset version id missing");
if (result.modelVersionId !== "toy-run-001-candidate") throw new Error("Model version id missing");
if (result.inferenceEventId !== "inference-001") throw new Error("Inference event id missing");
if (result.inferenceRunId !== "infer-run-001") throw new Error("Inference run id missing");
if (result.batchId !== "infer-run-001") throw new Error("Batch id missing");
if (result.reviewItemId !== "review-001") throw new Error("Review item id missing");
if (result.modelArtifactId !== "dataset@toy-001-run-001-linear-head") throw new Error("Model artifact id missing");
if (result.thresholdStrategyId !== "dataset@toy-001-run-001-threshold-strategy") {
  throw new Error("Threshold strategy id missing");
}
if (result.topK.length !== 2 || result.topK[0].label !== "red_square") throw new Error("Top-k not normalized");
if (result.decision.value !== "accept") throw new Error("Decision value not normalized");
if (result.decision.oodScore !== 0.22) throw new Error("OOD score not normalized");
if (result.nearestNeighbors[0].sampleId !== "sample-002") throw new Error("Nearest neighbor id missing");

for (const decision of ["accept", "abstain", "reject_ood"]) {
  const normalized = normalizeInferenceResult({ result: { decision: { decision } } });
  if (normalized.decision.value !== decision) throw new Error(`${decision} decision not preserved`);
}

console.log("inference api client smoke passed");
