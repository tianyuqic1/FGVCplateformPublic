// Real MinIO + Python scanner + Go publisher against an isolated database only.
import assert from "node:assert/strict";
import { deflateSync } from "node:zlib";
import { randomUUID } from "node:crypto";
const root = process.env.PUBLICATION_TEST_API || "http://localhost:18002";
assert.equal(root, "http://localhost:18002", "refusing normal application database");
async function request(path, body, method) {
  const response = await fetch(root + path, { method: method || (body ? "POST" : "GET"), headers: body && !(body instanceof FormData) ? { "Content-Type": "application/json" } : {}, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined });
  const value = await response.json(); return { status: response.status, value };
}
async function api(path, body, method) { const r = await request(path, body, method); assert.ok(r.status < 300, `${path}: ${r.status} ${JSON.stringify(r.value)}`); return r.value; }
function crc(bytes) { let c = 0xffffffff; for (const b of bytes) { c ^= b; for (let i = 0; i < 8; i++) c = (c >>> 1) ^ (c & 1 ? 0xedb88320 : 0); } return (c ^ 0xffffffff) >>> 0; }
function chunk(type, bytes) { const tag = Buffer.from(type); const size = Buffer.alloc(4); size.writeUInt32BE(bytes.length); const checksum = Buffer.alloc(4); checksum.writeUInt32BE(crc(Buffer.concat([tag, bytes]))); return Buffer.concat([size, tag, bytes, checksum]); }
function png(seed) { const header = Buffer.alloc(13); header.writeUInt32BE(16, 0); header.writeUInt32BE(16, 4); header[8] = 8; header[9] = 2; const pixels = Buffer.alloc(16 * 49); for (let y = 0; y < 16; y++) for (let x = 0; x < 48; x++) pixels[y * 49 + 1 + x] = (seed * 31 + x * 17 + y * 7) % 256; return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]), chunk("IHDR", header), chunk("IDAT", deflateSync(pixels)), chunk("IEND", Buffer.alloc(0))]); }
const prefix = "/api/annotation";
const project = await api(`${prefix}/projects`, { name: `发布验收 · ${randomUUID().slice(0, 8)}`, classes: [{ id: "a", name: "Alpha" }, { id: "b", name: "Beta" }], method: "A", domain: "general" });
async function upload(seed, label) { const form = new FormData(); form.set("image", new Blob([png(seed)], { type: "image/png" }), `sample-${seed}.png`); const task = await api(`${prefix}/projects/${project.id}/images`, form); const id = task.id || task.task?.id; assert.ok(id); await api(`${prefix}/tasks/${id}/confirm`, { label, actor: "隔离发布验收" }); return id; }
const ids = [];
for (let i = 0; i < 24; i++) ids.push(await upload(i, i < 12 ? "a" : "b"));
const first = await api(`${prefix}/publications/candidates?source=annotation&scope=${project.id}&page=1`);
const second = await api(`${prefix}/publications/candidates?source=annotation&scope=${project.id}&page=2`);
assert.equal(first.items.length, 12); assert.equal(second.items.length, 12); assert.equal(first.total, 24);
let input = { id: randomUUID(), source: "annotation", project_id: project.id, dataset_id: "", base_id: "", name: project.name, ids, train_only: false, split: { train: 80, val: 10, test: 10, seed: 42 }, mapping: {}, notes: "真实对象存储与扫描发布验收；不启动训练" };
assert.equal((await request(`${prefix}/publications/preview`, { ...input, id: randomUUID(), split: { train: 90, val: 10, test: 10, seed: 42 } })).status, 422);
const preview = await api(`${prefix}/publications/preview`, input);
assert.equal(preview.preview.changes.added_count, 24);
assert.deepEqual(preview.preview.changes.split_counts, { train: { Alpha: 10, Beta: 10 }, val: { Alpha: 1, Beta: 1 }, test: { Alpha: 1, Beta: 1 } });
await Promise.all([api(`${prefix}/publications/${input.id}/publish`, {}), api(`${prefix}/publications/${input.id}/publish`, {})]);
async function wait(id) { for (let i = 0; i < 90; i++) { const jobs = await api(`${prefix}/publications/?page=1`); const job = jobs.items.find(j => j.id === id); if (job?.status === "published") return job; if (job?.status === "failed") throw new Error(job.error); await new Promise(resolve => setTimeout(resolve, 1000)); } throw new Error("publication timed out"); }
const published = await wait(input.id); const version = published.result.version;
assert.equal(version.version_number, 1); assert.equal(version.sample_count, 24);
const baseline = await api(`/api/dataset-versions/${version.dataset_version_id}/sample-previews?limit=24`);
assert.equal((await api(`${prefix}/publications/candidates?source=annotation&scope=${project.id}`)).total, 0);
const nextId = await upload(50, "a");
input = { ...input, id: randomUUID(), name: "", dataset_id: version.dataset_id, base_id: version.dataset_version_id, ids: [nextId], train_only: true };
const expansion = await api(`${prefix}/publications/preview`, input);
assert.equal(expansion.preview.changes.split_counts.train.Alpha, 11);
await api(`${prefix}/publications/${input.id}/publish`, {}); const expanded = await wait(input.id);
assert.equal(expanded.result.version.version_number, 2); assert.equal(expanded.result.version.sample_count, 25);
assert.deepEqual(await api(`/api/dataset-versions/${version.dataset_version_id}/sample-previews?limit=24`), baseline, "baseline preview mutated");
// Registered labels are never repaired: same image with a different human label is rejected.
const conflictProject = await api(`${prefix}/projects`, { name: "冲突拦截验收", classes: project.classes, method: "A", domain: "general" });
const form = new FormData(); form.set("image", new Blob([png(0)], { type: "image/png" }), "conflict.png");
const conflict = await api(`${prefix}/projects/${conflictProject.id}/images`, form); await api(`${prefix}/tasks/${conflict.id}/confirm`, { label: "b", actor: "隔离验收" });
const bad = await request(`${prefix}/publications/preview`, { ...input, id: randomUUID(), project_id: conflictProject.id, ids: [conflict.id] });
assert.equal(bad.status, 422); assert.match(bad.value.detail, /标签冲突/);
console.log(JSON.stringify({ passed: true, project_id: project.id, dataset_id: version.dataset_id, v1: version.dataset_version_id, v2: expanded.result.version.dataset_version_id, checks: ["real MinIO upload", "12-row pagination", "ratio validation", "stratified 80/10/10 split", "Python manifest scan", "idempotent publish", "registered samples removed from waiting area", "append train only", "old version unchanged", "conflicting labels blocked"] }, null, 2));
