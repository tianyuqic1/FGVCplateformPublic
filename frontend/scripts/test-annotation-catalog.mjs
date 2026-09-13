import assert from "node:assert/strict";
import { parseCatalog, catalogTemplate } from "../src/features/annotation/catalog.js";
assert.deepEqual(parseCatalog(JSON.stringify(catalogTemplate)), catalogTemplate);
assert.deepEqual(parseCatalog("\uFEFF" + JSON.stringify(catalogTemplate)), catalogTemplate);
for (const data of [null, [], { classes: [] }, { ...catalogTemplate, schema_version: 2 }, { ...catalogTemplate, classes: [{ id: "a", name: "A" }] }, { ...catalogTemplate, classes: [{ id: "a", name: "A" }, { id: "a", name: "B" }] }, { ...catalogTemplate, classes: [{ id: "a", name: "A" }, { id: "b", name: "A" }] }, { ...catalogTemplate, classes: [{ id: 1, name: "A" }, { id: "b", name: "B" }] }]) assert.throws(() => parseCatalog(JSON.stringify(data)));
assert.throws(() => parseCatalog("{bad json"));
assert.throws(() => parseCatalog(" ".repeat(512 * 1024 + 1)));
console.log("Catalog JSON validation: template, BOM, schema, count, duplicates, types, syntax and size passed.");
