import { extractJob, extractJobList, normalizeJob, normalizeJobStatus } from "../src/api/jobs.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const list = extractJobList({
  jobs: [
    {
      job_id: "job-001",
      job_type: "feature_extraction",
      state: "in_progress",
      dataset_id: "fixture-dataset",
      dataset_version_id: "dataset@fixture-001",
      progress_percent: 42,
      status_message: "extracting DINOv3 embeddings",
    },
  ],
});

const normalized = normalizeJob(list[0]);
assert(normalized.id === "job-001", "normalizes job_id");
assert(normalized.jobType === "feature_extraction", "normalizes job_type");
assert(normalized.status === "running", "maps in_progress to running");
assert(normalized.datasetId === "fixture-dataset", "normalizes dataset_id");
assert(normalized.datasetVersionId === "dataset@fixture-001", "normalizes dataset_version_id");
assert(normalized.progress === 42, "normalizes progress_percent");
assert(normalized.message === "extracting DINOv3 embeddings", "normalizes status_message");

const detail = normalizeJob(extractJob({ job: { id: "job-002", type: "calibration_sweep", status: "done" } }));
assert(detail.id === "job-002", "extracts wrapped job payload");
assert(detail.status === "succeeded", "maps done to succeeded");

assert(normalizeJobStatus("pending") === "queued", "maps pending to queued");
assert(normalizeJobStatus("cancelled") === "cancelled", "preserves cancelled");
assert(normalizeJobStatus("canceled") === "cancelled", "maps canceled to cancelled");
assert(normalizeJobStatus("error") === "failed", "maps error to failed");

console.log("jobs api client smoke passed");
