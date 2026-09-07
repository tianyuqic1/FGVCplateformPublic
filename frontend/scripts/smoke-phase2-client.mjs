import { normalizeModelVersion } from "../src/api/modelVersions.js";
import { normalizeMetricPoint } from "../src/api/trainingRuns.js";
import { mergeMetricPoints, TERMINAL_TRAINING_STATUSES } from "../src/features/training/useTrainingMetrics.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const first = normalizeMetricPoint({ id: 1, attempt_id: "attempt-a", metric_name: "train_loss", step: 1, value: 0.8 });
const duplicate = normalizeMetricPoint({ id: 2, attempt_id: "attempt-a", metric_name: "train_loss", step: 1, value: 0.7 });
const nextAttempt = normalizeMetricPoint({ id: 3, attempt_id: "attempt-b", metric_name: "train_loss", step: 1, value: 0.9 });
const merged = mergeMetricPoints([first], [duplicate, nextAttempt]);
assert(merged.length === 2, "metric retry must be idempotent per attempt/name/step");
assert(merged[0].value === 0.7, "new cursor point must replace the same metric identity");
assert(TERMINAL_TRAINING_STATUSES.has("succeeded") && !TERMINAL_TRAINING_STATUSES.has("running"), "terminal polling contract changed");

const version = normalizeModelVersion({
  model_version_id: "11111111-1111-4111-8111-111111111111",
  dataset_version_id: "dataset-version-db-id",
  dataset_version_key: "dataset@bird-v12",
  backbone_key: "imagenet_vits16_augreg_in21k_ft_in1k",
  status: "production",
  aliases: ["champion"],
  metrics: { accuracy: 0.91 },
  evaluation_context: { protocol_fingerprint: "abc" },
});
assert(version.datasetVersionKey === "dataset@bird-v12", "Dataset Version display key missing");
assert(version.backboneKey === "imagenet_vits16_augreg_in21k_ft_in1k", "stable backbone key missing");
assert(version.aliases[0] === "champion" && version.status === "production", "registry governance fields missing");

console.log("phase2 metric and model registry client smoke passed");
