import assert from "node:assert/strict";
import { groupModels, modelScope, toggleModelSelection } from "../src/features/model-versions/modelGroups.js";
const models = [
 {id:"a",datasetId:"birds",datasetName:"同名数据集",datasetVersionId:"v1",datasetVersionNumber:1},
 {id:"b",datasetId:"birds",datasetName:"同名数据集",datasetVersionId:"v1",datasetVersionNumber:1},
 {id:"c",datasetId:"birds",datasetName:"同名数据集",datasetVersionId:"v2",datasetVersionNumber:2},
 {id:"d",datasetId:"flowers",datasetName:"同名数据集",datasetVersionId:"v1",datasetVersionNumber:1},
];
const groups=groupModels(models);
assert.equal(groups.length,2);
assert.deepEqual(groups[0].versions.map(v=>v.number),[2,1]);
assert.equal(groups[0].versions[1].models.length,2);
assert.deepEqual(toggleModelSelection(["a"],models[1],models),["a","b"]);
assert.deepEqual(toggleModelSelection(["a","b"],models[2],models),["c"]);
assert.deepEqual(toggleModelSelection(["a"],models[3],models),["d"]);
assert.equal(modelScope({id:"missing"}),null);
assert.equal(groupModels([{id:"x"},{id:"y"}]).length,2);
console.log("Model grouping identity, data-version ordering, same-scope selection and missing identity passed.");
