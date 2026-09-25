import { describe, expect, it } from "vitest";
import { paginateItems, pageNumbers } from "./pagination.js";

describe("paginateItems", () => {
  it("clamps an invalid page and reports the visible range", () => {
    const result = paginateItems(
      Array.from({ length: 13 }, (_, index) => index + 1),
      99,
      6,
    );

    expect(result.page).toBe(3);
    expect(result.items).toEqual([13]);
    expect(result.start).toBe(13);
    expect(result.end).toBe(13);
  });

  it("keeps an empty list on a stable first page", () => {
    expect(paginateItems([], 3, 6)).toMatchObject({ page: 1, pageCount: 1, start: 0, end: 0 });
  });
});

describe("pageNumbers", () => {
  it("keeps the current page and both ends visible", () => {
    expect(pageNumbers(5, 10)).toEqual([1, null, 4, 5, 6, null, 10]);
  });
});
