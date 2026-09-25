import { describe, expect, it } from "vitest";
import { analyzeImageFolderFiles } from "./imageFolder.js";

function image(path) {
  return { name: path.split("/").at(-1), webkitRelativePath: path, size: 10 };
}

describe("analyzeImageFolderFiles", () => {
  it("recognizes a split ImageFolder and preserves its root name", () => {
    const result = analyzeImageFolderFiles([
      image("cub/train/albatross/one.jpg"),
      image("cub/train/auk/two.png"),
      image("cub/val/albatross/three.jpg"),
      image("cub/val/auk/four.jpg"),
    ]);

    expect(result).toMatchObject({
      valid: true,
      format: "split/class/image",
      rootName: "cub",
      classes: ["albatross", "auk"],
      splits: ["train", "val"],
      imageCount: 4,
    });
  });

  it("rejects folders without two valid classes", () => {
    const result = analyzeImageFolderFiles([image("cub/train/albatross/one.jpg")]);

    expect(result.valid).toBe(false);
    expect(result.error).toMatch(/至少包含两个类别/);
  });
});
