import {
  extractTrainingRun,
  extractTrainingRunList,
  normalizeTrainingRun,
} from "../src/api/trainingRuns.js";

const listPayload = {
  training_runs: [
    {
      run_id: "run-db-001",
      status: "succeeded",
      dataset_id: "toy",
      dataset_version_id: "dataset@toy-001",
      model_version_id: "toy-run-db-001-candidate",
      feature_artifact_id: "feature:dataset@toy-001:color_stats_v1:abc123",
      report_artifact_id: "run-db-001:training_report",
      metrics: {
        accuracy: 0.987,
        macro_f1: 0.954,
        expected_coverage: 0.91,
        expected_selective_risk: 0.01,
      },
    },
  ],
};

const runs = extractTrainingRunList(listPayload).map(normalizeTrainingRun);
if (runs.length !== 1) throw new Error("Expected one training run");
if (runs[0].id !== "run-db-001") throw new Error("Training run id was not normalized");
if (runs[0].status !== "succeeded") throw new Error("Training run status was not preserved");
if (runs[0].progress !== 100) throw new Error("Succeeded training run should be complete");
if (runs[0].metric !== "acc 98.7%") throw new Error("Training metric label was not derived");
if (runs[0].modelVersionId !== "toy-run-db-001-candidate") throw new Error("Model version id missing");

const detail = normalizeTrainingRun(extractTrainingRun({ training_run: listPayload.training_runs[0] }));
if (detail.featureArtifactId !== "feature:dataset@toy-001:color_stats_v1:abc123") {
  throw new Error("Feature artifact id missing");
}
if (detail.thresholdStrategyArtifactId !== null) {
  throw new Error("Missing threshold strategy should normalize to null");
}

console.log("training api client smoke passed");
