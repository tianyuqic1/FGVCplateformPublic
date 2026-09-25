import {
  activateAbstentionPolicy,
  deactivateAbstentionPolicy,
  listAbstentionPolicies,
  listAbstentionShadowDecisions,
  proposeAbstentionPolicy,
} from "../api/abstentionPolicies.js";
import { useDomainMutation } from "../query/useDomainMutation.js";
import { useDomainQuery } from "../query/useDomainQuery.js";

export function useAbstentionPolicies(filters = {}) {
  const enabled = filters.enabled !== false;
  const query = useDomainQuery({
    queryKey: ["abstention-policies", filters.datasetVersionId ?? "", filters.modelVersionId ?? "", filters.status ?? "", filters.limit ?? 0],
    queryFn: () => listAbstentionPolicies(filters),
    enabled,
    emptyValue: [],
  });

  return { policies: query.data, ...withoutData(query) };
}

export function useAbstentionShadowDecisions(policyId, filters = {}) {
  const query = useDomainQuery({
    queryKey: ["abstention-shadow-decisions", policyId ?? "", filters.diff ?? "", filters.limit ?? 0],
    queryFn: () => listAbstentionShadowDecisions(policyId, filters),
    enabled: Boolean(policyId),
    emptyValue: [],
  });

  return { shadowDecisions: query.data, ...withoutData(query) };
}

export function useProposeAbstentionPolicy() {
  const mutation = useDomainMutation(proposeAbstentionPolicy);
  return { status: mutation.status, error: mutation.error, propose: mutation.run, reset: mutation.reset };
}

export function useActivateAbstentionPolicy() {
  const mutation = useDomainMutation(({ policyId, input }) => activateAbstentionPolicy(policyId, input));
  return {
    status: mutation.status,
    error: mutation.error,
    result: mutation.result,
    activate: (policyId, input = {}) => mutation.run({ policyId, input }),
    reset: mutation.reset,
  };
}

export function useDeactivateAbstentionPolicy() {
  const mutation = useDomainMutation(({ policyId, input }) => deactivateAbstentionPolicy(policyId, input));
  return {
    status: mutation.status,
    error: mutation.error,
    result: mutation.result,
    deactivate: (policyId, input = {}) => mutation.run({ policyId, input }),
    reset: mutation.reset,
  };
}

function withoutData({ data: _data, ...query }) {
  return query;
}
