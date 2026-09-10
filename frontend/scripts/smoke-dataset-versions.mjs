import assert from "node:assert/strict";
import { datasetSnapshots, datasetVersionOptions, expandDataset, normalizeDataset, uploadImagefolder } from "../src/api/datasets.js";
const dataset = normalizeDataset({
  dataset_id: "stable-id", name: "鸟类识别", dataset_version_id: "version-2", latest_version_id: "version-2", version_number: 2,
  versions: [
    { dataset_version_id: "version-2", version_number: 2, parent_version_id: "version-1", has_weights: false, model_count: 0, training_status: "untrained", sample_count: 1200 },
    { dataset_version_id: "version-1", version_number: 1, has_weights: true, model_count: 2, models: [{ id: "model-a" }, { id: "model-b" }], training_status: "trained", sample_count: 1000 },
  ],
});
assert.deepEqual(datasetSnapshots([dataset]).map(item => item.datasetVersionId), ["version-2", "version-1"]);
assert.equal(normalizeDataset({classes: [], class_count: 3}).classes, 3);
assert.equal(dataset.name, "鸟类识别");
assert.equal(dataset.id, "stable-id");
assert.equal(dataset.versions[0].hasWeights, false);
assert.equal(dataset.versions[1].hasWeights, true);
assert.equal(dataset.versions[0].parentId, "version-1");
assert.deepEqual(datasetVersionOptions([dataset]).map(option => option.value), ["version-2", "version-1"]);
assert.equal(datasetVersionOptions([dataset])[0].detail, "暂无权重");
let request;
globalThis.fetch = async (url, options) => {
  request = { url, options };
  return { ok: true, json: async () => ({ version: { dataset_version_id: "version-3" } }) };
};
globalThis.XMLHttpRequest = class {
  upload = {};
  status = 202;
  responseText = JSON.stringify({ job: { id: "job-a", status: "queued" } });
  open() {}
  setRequestHeader() {}
  send(body) { request = { options: { body } }; this.onload(); }
};
await uploadImagefolder({ name: "鸟类识别", request_id: "request-a", files: [new File(["image"], "class/image.png")] });
assert.equal(request.options.body.get("name"), "鸟类识别");
assert.equal(request.options.body.get("request_id"), "request-a");
assert.equal(request.options.body.has("dataset_id"), false);
assert.equal(request.options.body.has("dataset_version_id"), false);
await expandDataset("stable-id", { baseVersionId: "version-2", requestId: "request-b", feedbackIds: ["reviewed-old-model-image"] });
assert.equal(request.url, "/api/datasets/stable-id/versions");
assert.equal(request.options.body.get("base_version_id"), "version-2");
assert.equal(request.options.body.get("request_id"), "request-b");
assert.deepEqual(JSON.parse(request.options.body.get("feedback_ids")), ["reviewed-old-model-image"]);
console.log("dataset versions client smoke passed");
