import {
  listAbstentionPolicies,
  listAbstentionShadowDecisions,
  normalizePolicy,
  normalizeShadowDecision,
  proposeAbstentionPolicy,
} from "../src/api/abstentionPolicies.js";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const rawPolicy = {
  policy_id: "policy-001",
  dataset_id: "toy",
  dataset_version_id: "dataset@toy-001",
  model_version_id: "toy-run-001-candidate",
  status: "shadow",
  target_selective_risk: 0.05,
  tau_conf: 0.82,
  tau_margin: 0.14,
  tau_ood: null,
  source_feedback_count: 7,
  metrics: {
    coverage: 0.72,
    selective_risk: 0.04,
    estimated_review_cost: 3,
    decision_diff_counts: { same: 5, abstain_to_accept: 2 },
  },
  selection_config: { selection_rule: "risk_constrained" },
  created_by: "local-operator",
};

const policy = normalizePolicy(rawPolicy);
assert(policy.id === "policy-001", "normalizes policy id");
assert(policy.datasetVersionId === "dataset@toy-001", "normalizes policy dataset version");
assert(policy.modelVersionId === "toy-run-001-candidate", "normalizes policy model version");
assert(policy.targetSelectiveRisk === 0.05, "normalizes target selective risk");
assert(policy.sourceFeedbackCount === 7, "normalizes source feedback count");
assert(policy.estimatedCoverage === 0.72, "falls back to metrics coverage");
assert(policy.selectionConfig.selection_rule === "risk_constrained", "normalizes selection config");

const rawShadow = {
  shadow_decision_id: "shadow-001",
  policy_id: "policy-001",
  inference_event_id: "inference-001",
  current_decision: "abstain",
  shadow_decision: "accept",
  decision_diff: "abstain_to_accept",
  score_snapshot: { confidence: 0.91, margin: 0.2 },
};

const shadow = normalizeShadowDecision(rawShadow);
assert(shadow.id === "shadow-001", "normalizes shadow id");
assert(shadow.policyId === "policy-001", "normalizes shadow policy id");
assert(shadow.decisionDiff === "abstain_to_accept", "normalizes shadow diff");
assert(shadow.scoreSnapshot.confidence === 0.91, "keeps score snapshot");

let requestedUrl = "";
let requestedOptions = {};
globalThis.window = {
  setTimeout,
  clearTimeout,
};
globalThis.fetch = async (url, options = {}) => {
  requestedUrl = url;
  requestedOptions = options;
  if (url.includes("/shadow-decisions")) {
    return {
      ok: true,
      json: async () => ({ shadow_decisions: [rawShadow] }),
    };
  }
  if (options.method === "POST") {
    return {
      ok: true,
      json: async () => ({ policy: rawPolicy }),
    };
  }
  return {
    ok: true,
    json: async () => ({ policies: [rawPolicy] }),
  };
};

const proposed = await proposeAbstentionPolicy({
  dataset_version_id: "dataset@toy-001",
  model_version_id: "toy-run-001-candidate",
  target_selective_risk: 0.05,
  review_cost_per_item: 1.0,
  created_by: "local-operator",
});
assert(requestedUrl.endsWith("/api/abstention-policies/propose"), "propose endpoint missing");
assert(requestedOptions.method === "POST", "propose method missing");
assert(JSON.parse(requestedOptions.body).target_selective_risk === 0.05, "propose body target risk missing");
assert(proposed.id === "policy-001", "propose normalizes returned policy");

const policies = await listAbstentionPolicies({
  datasetVersionId: "dataset@toy-001",
  modelVersionId: "toy-run-001-candidate",
  status: "all",
  limit: 5,
});
assert(requestedUrl.includes("/api/abstention-policies?"), "policy list endpoint missing");
assert(requestedUrl.includes("dataset_version_id=dataset%40toy-001"), "policy dataset scope missing");
assert(requestedUrl.includes("model_version_id=toy-run-001-candidate"), "policy model scope missing");
assert(requestedUrl.includes("status=all"), "policy status filter missing");
assert(requestedUrl.includes("limit=5"), "policy limit missing");
assert(policies[0].id === "policy-001", "list normalizes policies");

const shadows = await listAbstentionShadowDecisions("policy-001", { diff: "changed", limit: 12 });
assert(requestedUrl.includes("/api/abstention-policies/policy-001/shadow-decisions?"), "shadow endpoint missing");
assert(requestedUrl.includes("diff=changed"), "shadow diff filter missing");
assert(requestedUrl.includes("limit=12"), "shadow limit missing");
assert(shadows[0].id === "shadow-001", "list normalizes shadows");

console.log("abstention api client smoke passed");
