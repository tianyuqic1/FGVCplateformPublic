import assert from "node:assert/strict";
import { readdir, stat } from "node:fs/promises";
import { join } from "node:path";

const assetsDir = new URL("../dist/assets/", import.meta.url);
const files = await readdir(assetsDir);
const sizes = await Promise.all(
  files.map(async (name) => ({ name, bytes: (await stat(join(assetsDir.pathname, name))).size })),
);
const entry = sizes.find((item) => /^index-[^.]+\.js$/.test(item.name));
const firstParty = sizes.filter(
  (item) => item.name.endsWith(".js") && !/^(charts|chart-renderer|react|icons)-/.test(item.name),
);
const largestFirstParty = firstParty.toSorted((a, b) => b.bytes - a.bytes)[0];
const cssBytes = sizes.filter((item) => item.name.endsWith(".css")).reduce((total, item) => total + item.bytes, 0);

assert.ok(entry, "没有找到入口 JavaScript 产物");
assert.ok(entry.bytes <= 80 * 1024, `入口包超出 80 KiB：${entry.name} ${entry.bytes} bytes`);
assert.ok(
  largestFirstParty.bytes <= 140 * 1024,
  `业务路由包超出 140 KiB：${largestFirstParty.name} ${largestFirstParty.bytes} bytes`,
);
assert.ok(cssBytes <= 160 * 1024, `CSS 总量超出 160 KiB：${cssBytes} bytes`);

console.log(
  `Bundle budget passed: entry=${entry.bytes}B, largest-route=${largestFirstParty.name}:${largestFirstParty.bytes}B, css=${cssBytes}B`,
);
