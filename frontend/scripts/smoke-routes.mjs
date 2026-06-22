import { spawn } from "node:child_process";

const routes = [
  "/",
  "/datasets",
  "/datasets/bird?tab=overview",
  "/datasets/bird?tab=classes",
  "/datasets/bird?tab=samples",
  "/datasets/bird?tab=features",
  "/datasets/bird?tab=ood",
  "/training",
  "/training/run-042",
  "/inference",
  "/weights",
  "/review",
  "/feedback",
  "/models",
  "/pipelines",
  "/pipelines?job_id=__smoke__",
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
