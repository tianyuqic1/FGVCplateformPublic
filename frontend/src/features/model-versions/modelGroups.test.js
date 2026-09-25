import { describe, expect, it } from "vitest";
import { toggleModelSelection } from "./modelGroups.js";

describe("model comparison selection", () => {
  const first = { id: "first", datasetId: "birds", datasetVersionId: "v1" };
  const second = { id: "second", datasetId: "birds", datasetVersionId: "v1" };
  const otherVersion = { id: "other", datasetId: "birds", datasetVersionId: "v2" };

  it("retains same-version selections from an earlier page", () => {
    expect(toggleModelSelection([first.id], second, [first, second])).toEqual([first.id, second.id]);
  });

  it("starts a new selection when the data version changes", () => {
    expect(toggleModelSelection([first.id], otherVersion, [first, otherVersion])).toEqual([otherVersion.id]);
  });
});
