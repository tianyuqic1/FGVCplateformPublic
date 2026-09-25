import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";

const marker = "/app/node_modules/.finevision-lock-sha256";
const currentHash = createHash("sha256").update(readFileSync("/app/package-lock.json")).digest("hex");
let installedHash = "";
try { installedHash = readFileSync(marker, "utf8").trim(); } catch { /* Fresh or legacy image. */ }

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: "inherit", cwd: "/app" });
    const stop = () => child.kill("SIGTERM");
    process.once("SIGTERM", stop);
    child.once("error", reject);
    child.once("exit", (code) => {
      process.removeListener("SIGTERM", stop);
      if (code === 0) resolve();
      else reject(new Error(`${command} ${args.join(" ")} exited with ${code}`));
    });
  });
}

if (installedHash !== currentHash) {
  process.stderr.write("[frontend] package-lock.json 与镜像依赖不一致，正在执行 npm ci；失败时请运行 docker compose up -d --build frontend。\n");
  await run("npm", ["ci", "--no-audit", "--no-fund"]);
  writeFileSync(marker, `${currentHash}\n`);
}
await run("npm", ["run", "dev", "--", "--host", "0.0.0.0"]);
