import { afterEach, describe, expect, it, vi } from "vitest";
import { searchEntities } from "./search.js";

afterEach(() => vi.unstubAllGlobals());

describe("entity search", () => {
  it("uses real API records and builds scoped routes", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ items: [
      { kind: "dataset", id: "cub-200", label: "CUB-200", hint: "数据集" },
      { kind: "review", id: "review-1", label: "样本 1", hint: "人工复核" },
      { kind: "unknown", id: "bad", label: "无效", hint: "无效" },
    ] }) });
    vi.stubGlobal("fetch", fetch);
    const items = await searchEntities("CUB-200");
    expect(fetch.mock.calls[0][0]).toContain("/api/search?q=CUB-200");
    expect(items.map(({ to }) => to)).toEqual(["/datasets/cub-200", "/review/review-1"]);
  });
});
