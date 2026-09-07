import { spawn } from "node:child_process";

const routes = [
  "/",
  "/datasets",
  "/datasets/route-availability-dataset?tab=overview",
  "/datasets/route-availability-dataset?tab=classes",
  "/datasets/route-availability-dataset?tab=samples",
  "/datasets/route-availability-dataset?tab=features",
  "/datasets/route-availability-dataset?tab=ood",
  "/training",
  "/training/route-availability-run",
  "/inference",
  "/weights",
  "/review",
  "/feedback",
  "/models",
  "/models/compare?ids=11111111-1111-4111-8111-111111111111,22222222-2222-4222-8222-222222222222",
  "/models/11111111-1111-4111-8111-111111111111",
  "/pipelines",
  "/pipelines?job_id=route-availability-job",
];

const port = process.env.SMOKE_PORT ?? "4173";
const baseUrl = `http://127.0.0.1:${port}`;
const child = spawn(
  process.platform === "win32" ? "npx.cmd" : "npx",
  ["vite", "preview", "--host", "127.0.0.1", "--port", port, "--strictPort"],
  { stdio: ["ignore", "pipe", "pipe"] },
);

let output = "";
child.stdout.on("data", (chunk) => {
  output += chunk.toString();
});
child.stderr.on("data", (chunk) => {
  output += chunk.toString();
});

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForServer() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(baseUrl);
      if (response.ok) return;
    } catch {
      await sleep(250);
    }
  }
  throw new Error(`Vite preview did not start on ${baseUrl}\n${output}`);
}

try {
  await waitForServer();
  console.log("Route availability smoke only: verifies Vite preview returns HTTP 200 for SPA routes.");
  for (const route of routes) {
    const response = await fetch(`${baseUrl}${route}`);
    if (!response.ok) {
      throw new Error(`${response.status} ${route}`);
    }
    console.log(`${response.status} ${route}`);
  }
} finally {
  child.kill("SIGTERM");
}
