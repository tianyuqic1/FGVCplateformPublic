// Requires Playwright and Chromium; set PLAYWRIGHT_MODULE to a bundled module
// path if Playwright is not installed in this frontend's node_modules.
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
const fixture = await mkdtemp(path.join(tmpdir(), "finevision-import-ui-"));
const page = await browser.newPage();
page.setDefaultTimeout(15000);
page.setDefaultNavigationTimeout(15000);
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
let jobs = [];
let releaseUpload;
const uploadGate = new Promise((resolve) => { releaseUpload = resolve; });
let uploading = false;
const queued = { id: "import-ui-job", name: "import-ui-test", request_id: "ab3e0384-42e9-4eb4-aa3a-ced378e1850a", status: "queued" };
try {
  await Promise.all(["a", "b"].map((label) => mkdir(path.join(fixture, label))));
  await Promise.all(Array.from({ length: 600 }, (_, index) => writeFile(path.join(fixture, index % 2 ? "a" : "b", `${index}.png`), "fake image: API mocked for UI checks")));
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/datasets/upload-imagefolder") {
      uploading = true;
      await uploadGate;
      jobs = [queued];
      await route.fulfill({ status: 202, json: { job: queued } });
    } else if (url.pathname === "/api/dataset-imports") {
      await route.fulfill({ json: { jobs } });
    } else if (url.pathname === "/api/datasets") {
      await route.fulfill({ json: { datasets: jobs.some((job) => job.status === "succeeded") ? [{ id: "import-ui-test", name: "import-ui-test", status: "ready", sample_count: 600, class_count: 2 }] : [] } });
    } else {
      await route.fulfill({ json: {} });
    }
  });
  await page.goto(`${process.env.SMOKE_BASE_URL || "http://127.0.0.1:5174"}/datasets`);
  await page.getByRole("button", { name: "导入数据集", exact: true }).click();
  await page.locator('input[type="file"]').setInputFiles(fixture);
  await page.getByLabel("数据集名称", { exact: true }).fill("import-ui-test");
  await page.getByRole("button", { name: "上传并导入", exact: true }).click();
  await page.getByRole("button", { name: "停止上传", exact: true }).waitFor();
  await page.getByRole("button", { name: "可训练", exact: true }).click();
  assert.match(await page.getByRole("button", { name: "可训练", exact: true }).getAttribute("class"), /active/, "page remains interactive during upload");
  assert.ok(uploading);
  releaseUpload();
  await page.getByRole("heading", { name: "后台导入任务" }).waitFor();
  await page.getByText("排队中", { exact: true }).last().waitFor();
  await page.reload();
  await page.getByText("排队中", { exact: true }).last().waitFor();
  jobs = [{ ...queued, status: "running" }];
  await page.getByText("校验与入库中", { exact: true }).waitFor();
  jobs = [{ ...queued, status: "succeeded", result: { dataset: { dataset_id: "import-ui-test" }, version: { version_number: 1 } } }];
  await page.getByRole("link", { name: "打开数据集", exact: true }).waitFor();
  await page.locator(".dataset-group summary").getByText("import-ui-test", { exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "打开数据集", exact: true }).getAttribute("href"), "/datasets/import-ui-test");
  jobs = [{ ...queued, id: "failed-job", status: "failed", error: "图片损坏" }];
  await page.getByText("导入失败", { exact: true }).waitFor();
  assert.ok((await page.locator("body").innerText()).includes("图片损坏"));
  assert.deepEqual(errors, []);
  console.log("dataset import UI smoke passed: responsive upload, reload, queue progress, refresh, failure");
} finally {
  releaseUpload();
  await browser.close();
  await rm(fixture, { recursive: true, force: true });
}
