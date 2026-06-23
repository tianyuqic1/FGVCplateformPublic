import { useCallback, useEffect, useState } from "react";
import {
  listAbstentionPolicies,
  listAbstentionShadowDecisions,
  proposeAbstentionPolicy,
} from "../api/abstentionPolicies.js";

export function useAbstentionPolicies(filters = {}) {
  const [state, setState] = useState({ policies: [], loading: true, error: null });

  const refresh = useCallback(() => {
    let active = true;
    setState((current) => ({ ...current, loading: true, error: null }));
    listAbstentionPolicies(filters)
      .then((policies) => {
        if (!active) return;
        setState({ policies, loading: false, error: null });
      })
      .catch((error) => {
        if (!active) return;
        setState({ policies: [], loading: false, error });
      });
    return () => {
      active = false;
    };
  }, [filters.datasetVersionId, filters.modelVersionId, filters.status, filters.limit]);

  useEffect(() => refresh(), [refresh]);

  return { ...state, refresh };
}

export function useAbstentionShadowDecisions(policyId, filters = {}) {
  const [state, setState] = useState({ shadowDecisions: [], loading: Boolean(policyId), error: null });

  const refresh = useCallback(() => {
    if (!policyId) {
      setState({ shadowDecisions: [], loading: false, error: null });
      return () => {};
    }
    let active = true;
    setState((current) => ({ ...current, loading: true, error: null }));
    listAbstentionShadowDecisions(policyId, filters)
      .then((shadowDecisions) => {
        if (!active) return;
        setState({ shadowDecisions, loading: false, error: null });
      })
      .catch((error) => {
        if (!active) return;
        setState({ shadowDecisions: [], loading: false, error });
      });
    return () => {
      active = false;
    };
  }, [policyId, filters.diff, filters.limit]);

  useEffect(() => refresh(), [refresh]);

  return { ...state, refresh };
}

export function useProposeAbstentionPolicy() {
  const [state, setState] = useState({ status: "idle", error: null });

  const propose = useCallback(async (input) => {
    setState({ status: "submitting", error: null });
    try {
      const policy = await proposeAbstentionPolicy(input);
      setState({ status: "succeeded", error: null });
      return policy;
    } catch (error) {
      setState({ status: "failed", error });
      throw error;
    }
  }, []);

  return { ...state, propose };
}
