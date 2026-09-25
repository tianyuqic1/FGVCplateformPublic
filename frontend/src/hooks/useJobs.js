import { getJob, listJobs } from "../api/jobs.js";
import { useDomainQuery } from "../query/useDomainQuery.js";

function fallbackJob(jobId) {
  return jobId ? { id: jobId, jobType: "job", status: "loading", progress: 0 } : null;
}

export function useRecentJobs(limit = 5) {
  const query = useDomainQuery({
    queryKey: ["jobs", "recent", limit],
    queryFn: async () => (await listJobs()).slice(0, limit),
    emptyValue: [],
  });

  return { jobs: query.data, ...withoutData(query) };
}

export function useJob(jobId) {
  const query = useDomainQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId),
    enabled: Boolean(jobId),
    emptyValue: fallbackJob(jobId),
    refetchInterval: (state) => {
      const status = state.state.data?.status;
      return ["queued", "running", "loading"].includes(status) ? 3_000 : false;
    },
  });

  return { job: query.data, ...withoutData(query) };
}

function withoutData({ data: _data, ...query }) {
  return query;
}
