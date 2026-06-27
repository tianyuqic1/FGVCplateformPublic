import { useCallback, useEffect, useState } from "react";
import {
  activateAbstentionPolicy,
  deactivateAbstentionPolicy,
  listAbstentionPolicies,
  listAbstentionShadowDecisions,
  proposeAbstentionPolicy,
} from "../api/abstentionPolicies.js";

export function useAbstentionPolicies(filters = {}) {
  const enabled = filters.enabled !== false;
  const [state, setState] = useState({ policies: [], loading: enabled, error: null });

  const refresh = useCallback(() => {
    if (!enabled) {
      setState({ policies: [], loading: false, error: null });
      return () => {};
    }
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
  }, [enabled, filters.datasetVersionId, filters.modelVersionId, filters.status, filters.limit]);

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

export function useActivateAbstentionPolicy() {
  const [state, setState] = useState({ status: "idle", error: null, result: null });

  const activate = useCallback(async (policyId, input = {}) => {
    setState({ status: "submitting", error: null, result: null });
    try {
      const result = await activateAbstentionPolicy(policyId, input);
      setState({ status: "succeeded", error: null, result });
      return result;
    } catch (error) {
      setState({ status: "failed", error, result: null });
      throw error;
    }
  }, []);

  return { ...state, activate };
}

export function useDeactivateAbstentionPolicy() {
  const [state, setState] = useState({ status: "idle", error: null, result: null });

  const deactivate = useCallback(async (policyId, input = {}) => {
    setState({ status: "submitting", error: null, result: null });
    try {
      const result = await deactivateAbstentionPolicy(policyId, input);
      setState({ status: "succeeded", error: null, result });
      return result;
    } catch (error) {
      setState({ status: "failed", error, result: null });
      throw error;
    }
  }, []);

  return { ...state, deactivate };
}
