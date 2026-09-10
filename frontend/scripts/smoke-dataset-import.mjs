import assert from "node:assert/strict";
import { uploadImagefolder } from "../src/api/datasets.js";

let sends = 0;
let fields;
class FakeXHR {
  upload = {};
  status = 202;
  responseText = JSON.stringify({ job: { id: "job-1", status: "queued" } });
  open() {}
  setRequestHeader() {}
  send(form) {
    sends++;
    fields = [...form.entries()];
    this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
    this.upload.onload?.();
    this.onload();
  }
  abort() { this.onabort?.(); }
}
globalThis.XMLHttpRequest = FakeXHR;
const files = Array.from({ length: 301 }, (_, i) => {
  const file = new File(["image"], `${i}.jpg`);
  Object.defineProperty(file, "webkitRelativePath", { value: `folder/class${i % 2}/${i}.jpg` });
  return file;
});
let ticks = 0;
const timer = setInterval(() => ticks++, 0);
const progress = [];
const result = await uploadImagefolder({ dataset_id: "birds", dataset_version_id: "birds-v1", files, onProgress: (value) => progress.push(value) });
clearInterval(timer);
assert.equal(result.job.id, "job-1");
assert.ok(ticks >= 3, "form construction must yield to UI between batches");
assert.equal(fields.filter(([key]) => key === "files").length, files.length);
assert.equal(fields[2][1].name, "folder/class0/0.jpg");
assert.ok(progress.some((p) => p.stage === "uploading" && p.percent === 50));
assert.ok(progress.some((p) => p.stage === "persisting"));
const controller = new AbortController();
controller.abort();
await assert.rejects(uploadImagefolder({ dataset_id: "birds", dataset_version_id: "v2", files, signal: controller.signal }), { name: "AbortError" });
assert.equal(sends, 1, "cancelled upload never sends");
await assert.rejects(uploadImagefolder({ dataset_id: "birds", dataset_version_id: "v2", files: [{ size: 33 * 1024 * 1024 }] }), /32 MiB/);
assert.equal(sends, 1, "oversized files rejected before sending");
console.log("dataset import client smoke passed");
