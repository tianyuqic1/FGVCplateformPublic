import { useEffect, useMemo, useState } from "react";
import { getJob, listJobs } from "../api/jobs.js";
import { jobs as mockJobs } from "../data/mockData.js";

function mergeJob(apiJob) {
  const fallback = mockJobs.find((item) => item.id === apiJob.id) ?? {};
  return { ...fallback, ...apiJob };
}

function fallbackJob(jobId) {
  return mockJobs.find((item) => item.id === jobId) ?? mockJobs[0];
}

export function useRecentJobs(limit = 5) {
  const fallbackJobs = useMemo(() => mockJobs.slice(0, limit), [limit]);
  const [state, setState] = useState({
    jobs: fallbackJobs,
    source: "mock",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;
    setState({ jobs: fallbackJobs, source: "mock", loading: true, error: null });

    listJobs()
      .then((items) => {
        if (!active) return;
        const nextJobs = items.length > 0 ? items.map(mergeJob).slice(0, limit) : fallbackJobs;
        setState({
          jobs: nextJobs,
          source: items.length > 0 ? "api" : "mock",
          loading: false,
          error: null,
        });
      })
      .catch((error) => {
        if (!active) return;
        setState({ jobs: fallbackJobs, source: "mock", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, [fallbackJobs, limit]);

  return state;
}

export function useJob(jobId) {
  const initialJob = useMemo(() => fallbackJob(jobId), [jobId]);
  const [state, setState] = useState({
    job: initialJob,
    source: "mock",
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;
    const nextFallback = fallbackJob(jobId);
    setState({ job: nextFallback, source: "mock", loading: true, error: null });

    getJob(jobId)
      .then((item) => {
        if (!active) return;
        setState({ job: mergeJob(item), source: "api", loading: false, error: null });
      })
      .catch((error) => {
        if (!active) return;
        setState({ job: nextFallback, source: "mock", loading: false, error });
      });

    return () => {
      active = false;
    };
  }, [jobId]);

  return state;
}
