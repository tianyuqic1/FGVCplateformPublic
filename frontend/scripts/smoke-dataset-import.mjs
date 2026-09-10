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
const result = await uploadImagefolder({ name: "birds", request_id: "5fe3fa20-2cac-41e5-826d-8d9b2dba8cb9", files, onProgress: (value) => progress.push(value) });
clearInterval(timer);
assert.equal(result.job.id, "job-1");
assert.ok(ticks >= 3, "form construction must yield to UI between batches");
assert.equal(fields.filter(([key]) => key === "files").length, files.length);
assert.equal(fields[2][1].name, "folder/class0/0.jpg");
assert.ok(progress.some((p) => p.stage === "uploading" && p.percent === 50));
assert.ok(progress.some((p) => p.stage === "persisting"));
const controller = new AbortController();
controller.abort();
await assert.rejects(uploadImagefolder({ name: "birds", request_id: "ab3e0384-42e9-4eb4-aa3a-ced378e1850a", files, signal: controller.signal }), { name: "AbortError" });
assert.equal(sends, 1, "cancelled upload never sends");
await assert.rejects(uploadImagefolder({ name: "birds", request_id: "ab3e0384-42e9-4eb4-aa3a-ced378e1850a", files: [{ size: 33 * 1024 * 1024 }] }), /32 MiB/);
assert.equal(sends, 1, "oversized files rejected before sending");
console.log("dataset import client smoke passed");
