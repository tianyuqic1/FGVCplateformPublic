import { useCallback, useEffect, useState } from "react";
import { getModelVersion, listModelVersions } from "../../api/modelVersions.js";

export function useModelVersions(filters = {}) {
  const fingerprint = JSON.stringify(filters);
  const [state, setState] = useState({ versions: [], loading: true, error: null });
  const refresh = useCallback(() => {
    let active = true;
    setState((current) => ({ ...current, loading: true, error: null }));
    listModelVersions(filters)
      .then((versions) => active && setState({ versions, loading: false, error: null }))
      .catch((error) => active && setState({ versions: [], loading: false, error }));
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fingerprint]);
  useEffect(() => refresh(), [refresh]);
  return { ...state, refresh };
}
export function useModelVersion(id) {
  const [state, setState] = useState({ version: null, loading: Boolean(id), error: null });
  const refresh = useCallback(() => {
    if (!id) return undefined;
    let active = true;
    setState((current) => ({ ...current, loading: true, error: null }));
    getModelVersion(id)
      .then((version) => active && setState({ version, loading: false, error: null }))
      .catch((error) => active && setState({ version: null, loading: false, error }));
    return () => { active = false; };
  }, [id]);
  useEffect(() => refresh(), [refresh]);
  return { ...state, refresh };
}
