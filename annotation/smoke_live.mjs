// Explicit live acceptance. Preparing fixtures never calls a paid model.
// --queue submits ONE previously unattempted image; repeated invocations do not
// requeue unknown/failed results. Reference labels come from the training split.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.dirname(fileURLToPath(import.meta.url));
const benchmarkRoot = process.env.ANNOTATION_BENCHMARK_ROOT;
if (!benchmarkRoot) throw new Error("ANNOTATION_BENCHMARK_ROOT must point to the private benchmark fixture directory");
const data = path.join(benchmarkRoot, "cub");
const base = process.env.ANNOTATION_API || "http://127.0.0.1:8001/api/annotation";
const fixtureFile = path.join(root, "runtime/live-acceptance.json");
async function api(url, body) {
  const r = await fetch(base + url, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`);
  return r.status === 204 ? null : r.json();
}
async function upload(project, source, name) {
  const form = new FormData(); form.append("image", new Blob([fs.readFileSync(source)]), name);
  const r = await fetch(`${base}/projects/${project}/images`, { method: "POST", body: form });
  assert.equal(r.status, 201, await r.clone().text()); return r.json();
}
let fixture;
if (fs.existsSync(fixtureFile)) fixture = JSON.parse(fs.readFileSync(fixtureFile));
else {
  const classes = JSON.parse(fs.readFileSync(path.join(data, "round-1/classes.json")));
  const references = JSON.parse(fs.readFileSync(path.join(data, "references.json")));
  const samples = JSON.parse(fs.readFileSync(path.join(data, "round-1/samples.json")));
  const project = await api("/projects", { name: "验收 · CUB 鸟类标注", classes, method: "D", domain: "birds" });
  fixture = { project_id: project.id, references: [], queries: [] };
  fs.mkdirSync(path.dirname(fixtureFile), { recursive: true });
  fs.writeFileSync(fixtureFile, JSON.stringify(fixture, null, 2));
  for (const [i, ref] of references.filter(r => r.label === references[0].label).slice(0, 10).entries()) {
    assert.equal(ref.confirmation_source, "dataset_train_ground_truth");
    const task = await upload(project.id, ref.image, `reference-${String(i + 1).padStart(2, "0")}.jpg`);
    await api(`/tasks/${task.id}/confirm`, { label: ref.label, actor: "系统验收 · 官方训练集标签" });
    fixture.references.push(task.id); fs.writeFileSync(fixtureFile, JSON.stringify(fixture, null, 2));
  }
  for (const [i, sample] of samples.slice(0, 3).entries()) {
    const task = await upload(project.id, sample.image, `query-${i + 1}.jpg`);
    fixture.queries.push(task.id); fs.writeFileSync(fixtureFile, JSON.stringify(fixture, null, 2));
  }
  const repeated = await upload(project.id, samples[0].image, "duplicate.jpg");
  assert.equal(repeated.id, fixture.queries[0]);
}
const first = await api(`/projects/${fixture.project_id}/tasks?page=1`);
const second = await api(`/projects/${fixture.project_id}/tasks?page=2`);
assert.equal(first.items.length, 12); assert.equal(first.total, 13); assert.equal(second.items.length, 1);
const exported = await api(`/projects/${fixture.project_id}/export`);
assert.equal(exported.samples.length, 10);
console.log(JSON.stringify({ fixture, total: exported.project.total, confirmed: exported.project.confirmed, indexed: exported.project.indexed }, null, 2));
if (process.argv.includes("--queue")) {
  assert.equal(exported.project.indexed, 10, "wait for real vector delivery before retrieval acceptance");
  console.log(await api(`/projects/${fixture.project_id}/queue`, { ids: [fixture.queries[0]], allow_remote: true }));
}
