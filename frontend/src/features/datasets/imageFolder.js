export const IMAGE_FOLDER_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".bmp", ".webp"]);
const IMAGE_FOLDER_SPLITS = new Set(["train", "val", "test"]);

function imageFolderRelativePath(file) {
  return (file.webkitRelativePath || file.name || "").replace(/\\/g, "/");
}

function imageFolderExtension(path) {
  const match = path.toLowerCase().match(/\.[^.]+$/);
  return match ? match[0] : "";
}

function ignoredFolderPath(path) {
  return path.split("/").some((part) => part === "__MACOSX" || part.startsWith("."));
}

function commonPathPrefix(paths) {
  if (!paths.length) return [];
  let prefix = paths[0].split("/").filter(Boolean);
  paths.slice(1).forEach((path) => {
    const parts = path.split("/").filter(Boolean);
    const next = [];
    for (let index = 0; index < Math.min(prefix.length, parts.length); index += 1) {
      if (prefix[index] !== parts[index]) break;
      next.push(prefix[index]);
    }
    prefix = next;
  });
  return prefix;
}

function validateImageFolderParts(partsList) {
  if (partsList.some((parts) => parts.length < 2)) return null;
  const topLevel = new Set(partsList.map((parts) => parts[0]));
  const hasSplit = [...topLevel].some((part) => IMAGE_FOLDER_SPLITS.has(part));
  const splitMode = hasSplit && [...topLevel].every((part) => IMAGE_FOLDER_SPLITS.has(part));
  if (splitMode) {
    if (partsList.some((parts) => parts.length < 3)) return null;
    const classes = [...new Set(partsList.map((parts) => parts[1]))].sort();
    if (classes.length < 2) return null;
    return {
      format: "split/class/image",
      classes,
      splits: [...topLevel].sort(),
      imageCount: partsList.length,
    };
  }
  if ([...topLevel].some((part) => IMAGE_FOLDER_SPLITS.has(part))) return null;
  const classes = [...topLevel].sort();
  if (classes.length < 2) return null;
  return {
    format: "class/image",
    classes,
    splits: [],
    imageCount: partsList.length,
  };
}

export function analyzeImageFolderFiles(files) {
  const selectedFiles = Array.from(files ?? []);
  if (!selectedFiles.length) {
    return { valid: false, error: "请选择一个包含图片的文件夹。", files: [], rootName: "" };
  }

  const usableFiles = [];
  let ignoredCount = 0;
  for (const file of selectedFiles) {
    const relativePath = imageFolderRelativePath(file);
    if (!relativePath || ignoredFolderPath(relativePath)) continue;
    if (!IMAGE_FOLDER_EXTENSIONS.has(imageFolderExtension(relativePath))) {
      ignoredCount += 1;
      continue;
    }
    usableFiles.push({ file, relativePath });
  }

  if (!usableFiles.length) {
    return {
      valid: false,
      error: "文件夹里没有 jpg、jpeg、png、bmp 或 webp 图片。",
      files: selectedFiles,
      rootName: "",
    };
  }

  const prefix = commonPathPrefix(usableFiles.map((item) => item.relativePath));
  let summary = null;
  for (let prefixLength = 0; prefixLength <= prefix.length; prefixLength += 1) {
    const partsList = usableFiles.map((item) => item.relativePath.split("/").filter(Boolean).slice(prefixLength));
    const candidate = validateImageFolderParts(partsList);
    if (candidate) summary = { ...candidate, strippedPrefix: prefix.slice(0, prefixLength) };
  }

  if (!summary) {
    return {
      valid: false,
      error: "未检测到合法 ImageFolder：请使用 class/image 或 train|val|test/class/image 结构，且至少包含两个类别。",
      files: selectedFiles,
      rootName: prefix[0] ?? "",
    };
  }

  return {
    valid: true,
    error: null,
    files: usableFiles.map((item) => item.file),
    ignoredCount,
    rootName: summary.strippedPrefix.at(-1) ?? prefix[0] ?? "local-imagefolder",
    ...summary,
  };
}
