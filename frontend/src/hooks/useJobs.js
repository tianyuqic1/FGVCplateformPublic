import { useEffect, useMemo, useState } from "react";
import { getJob, listJobs } from "../api/jobs.js";

function mergeJob(apiJob) {
  return apiJob;
}

function fallbackJob(jobId) {
  return jobId ? { id: jobId, jobType: "job", status: "loading", progress: 0 } : null;
}

export function useRecentJobs(limit = 5) {
  const [state, setState] = useState({
    jobs: [],
    source: "loading",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;
    setState({ jobs: [], source: "loading", loading: true, error: null });

    listJobs()
      .then((items) => {
        if (!active) return;
        setState({
          jobs: items.map(mergeJob).slice(0, limit),
          source: "api",
          loading: false,
          error: null,
        });
      })
      .catch((error) => {
        if (!active) return;
        setState({ jobs: [], source: "unavailable", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, [limit]);

  return state;
}

export function useJob(jobId) {
  const initialJob = useMemo(() => fallbackJob(jobId), [jobId]);
  const [state, setState] = useState({
    job: initialJob,
    source: "loading",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;
    const nextFallback = fallbackJob(jobId);
    setState({ job: nextFallback, source: "loading", loading: true, error: null });

    getJob(jobId)
      .then((item) => {
        if (!active) return;
        setState({ job: mergeJob(item), source: "api", loading: false, error: null });
      })
      .catch((error) => {
        if (!active) return;
        setState({ job: null, source: "unavailable", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, [jobId]);

  return state;
}
