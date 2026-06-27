import { extractDataset, extractDatasetList, normalizeDataset } from "../src/api/datasets.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const list = extractDatasetList({
  datasets: [
    {
      dataset_id: "cifar10-mini",
      display_name: "CIFAR-10 Mini",
      class_count: 10,
      sample_count: 460,
      dataset_version_id: "dataset@cifar10-mini-001",
      status: "ready",
    },
  ],
});

const normalized = normalizeDataset(list[0]);
assert(normalized.id === "cifar10-mini", "normalizes dataset_id");
assert(normalized.classes === 10, "normalizes class_count");
assert(normalized.images === 460, "normalizes sample_count");
assert(normalized.status === "ready", "keeps ready dataset status distinct from production");
assert(normalized.version === "dataset@cifar10-mini-001", "keeps dataset version");

const detail = normalizeDataset(extractDataset({ dataset: { id: "bird", classes: ["a", "b"] } }));
assert(detail.id === "bird", "extracts wrapped detail payload");
assert(detail.classes === 2, "counts class arrays");

console.log("api client smoke passed");
