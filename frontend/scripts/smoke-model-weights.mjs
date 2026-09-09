import assert from 'node:assert/strict';
import { listModelWeights } from '../src/api/modelWeights.js';
import { modelWeightLabel, modelWeightDetails } from '../src/features/weights/presentation.js';

globalThis.window = globalThis;
globalThis.fetch = async () => ({ ok: true, json: async () => ({ weights: [
  { state: 'managed', cached: true, cache_bytes: 86362376, size_bytes: 86362376, complete_file_count: 1 },
  { state: 'cached', cache_bytes: 1048576, complete_file_count: 1 },
  { state: 'partial', partial_bytes: 1048576 },
  { state: 'missing' },
  { state: 'new-server-state' },
] }) });
const weights = await listModelWeights();
assert.equal(modelWeightLabel(weights[0].state), '已纳入管理');
assert.match(modelWeightDetails(weights[0]), /82.4 MB.*节点缓存未上报/);
assert.doesNotMatch(modelWeightDetails(weights[0]), /未下载|1 files/);
assert.equal(modelWeightLabel(weights[1].state), '已缓存');
assert.match(modelWeightDetails(weights[1]), /1.0 MB.*1 个文件/);
assert.equal(modelWeightLabel(weights[2].state), '下载中/未完成');
assert.equal(modelWeightLabel(weights[3].state), '未缓存');
assert.equal(modelWeightLabel(weights[4].state), '状态未知');
console.log('model weight presentation smoke passed');
