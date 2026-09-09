import { useEffect, useState } from "react";
import { listInferenceModels } from "../api/inferenceModels.js";

export function useInferenceModels() {
  const [state, setState] = useState({ models: [], source: "loading" });
  useEffect(() => {
    let active = true;
    listInferenceModels()
      .then((models) => { if (active) setState({ models, source: "api" }); })
      .catch(() => { if (active) setState({ models: [], source: "unavailable" }); });
    return () => { active = false; };
  }, []);
  return state;
}
