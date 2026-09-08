import assert from "node:assert/strict";
import { selectPage, selectedPage } from "../src/design-system/components/selectPagination.js";

const options = Array.from({ length: 22 }, (_, index) => ({ value: `id-${index}`, label: `Dataset ${index}`, detail: `version-${index}` }));
assert.equal(selectPage(options, "", 1).items.length, 6);
assert.equal(selectPage(options, "", 2).items[0].value, "id-6");
assert.equal(selectPage(options, "", 4).items.length, 4);
assert.equal(selectPage(options, "", 99).page, 4);
assert.equal(selectPage(options.slice(0, 3), "", 4).page, 1);
assert.deepEqual(selectPage(options, " VERSION-21 ", 4).items, [options[21]]);
assert.deepEqual(selectPage(options, "id-19", 1).items, [options[19]]);
assert.deepEqual(selectPage(options, "DATASET 21", 1).items, [options[21]]);
assert.equal(selectPage(options, "no match", 4).start, 0);
assert.equal(selectPage([], "", 1).pageCount, 1);
assert.equal(selectedPage(options, "id-21"), 4);
assert.equal(selectedPage(options, "removed"), 1);
console.log("select pagination smoke passed");
