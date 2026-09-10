import { useEffect, useState } from "react";
import { getHardwareNode, listHardwareNodes } from "../../api/hardware.js";

export function useHardware(nodeId, window, automatic, revision) {
  const [state, setState] = useState({ nodes: [], data: null, key: "", loading: true, error: null });
  const key = `${nodeId}:${window}`;
  useEffect(() => {
    let active = true;
    let timer;
    let controller;
    let busy = false;
    async function refresh() {
      if (busy) return;
      busy = true;
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      setState((previous) => ({ ...previous, loading: true }));
      try {
        const [nodes, data] = await Promise.all([
          listHardwareNodes(controller.signal).then((result) => {
            if (active) setState((previous) => ({ ...previous, nodes: result.nodes ?? [] }));
            return result;
          }),
          nodeId ? getHardwareNode(nodeId, window, controller.signal) : Promise.resolve(null),
        ]);
        if (active) setState({ nodes: nodes.nodes ?? [], data, key, loading: false, error: null, loadedAt: Date.now(), serverTime: Date.parse(data?.server_time ?? nodes.server_time) });
      } catch (error) {
        if (active) setState((previous) => ({ ...previous, error, loading: false }));
      } finally {
        clearTimeout(timeout);
        busy = false;
        if (active && automatic) timer = setTimeout(refresh, document.hidden ? 30000 : 5000);
      }
    }
    function onVisibility() {
      clearTimeout(timer);
      if (automatic && !busy) refresh();
    }
    refresh();
    document.addEventListener("visibilitychange", onVisibility);
    return () => { active = false; clearTimeout(timer); controller?.abort(); document.removeEventListener("visibilitychange", onVisibility); };
  }, [nodeId, window, automatic, revision, key]);
  return { ...state, data: state.key === key ? state.data : null };
}
