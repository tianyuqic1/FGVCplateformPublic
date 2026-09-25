import { getReviewItem, listFeedbackItems, listReviewItemsPage, submitReviewOutcome } from "../api/reviews.js";
import { useDomainMutation } from "../query/useDomainMutation.js";
import { useDomainQuery } from "../query/useDomainQuery.js";

function emptyPagination(filters) {
  return {
    total: 0,
    totalKnown: false,
    limit: filters.limit ?? 50,
    offset: filters.offset ?? 0,
    hasMore: false,
    nextOffset: null,
  };
}

export function useReviewItems(filters = {}) {
  const query = useDomainQuery({
    queryKey: ["review-items", filters.status ?? "", filters.datasetId ?? "", filters.limit ?? 50, filters.offset ?? 0],
    queryFn: () => listReviewItemsPage(filters),
    emptyValue: { items: [], pagination: emptyPagination(filters) },
  });

  return {
    reviewItems: query.data.items,
    pagination: query.data.pagination,
    ...withoutData(query),
  };
}

export function useReviewItem(reviewItemId) {
  const query = useDomainQuery({
    queryKey: ["review-item", reviewItemId],
    queryFn: () => getReviewItem(reviewItemId),
    enabled: Boolean(reviewItemId),
    emptyValue: null,
  });

  return { reviewItem: query.data, ...withoutData(query) };
}

export function useSubmitReviewOutcome(reviewItemId) {
  const mutation = useDomainMutation((input) => submitReviewOutcome(reviewItemId, input));
  return { status: mutation.status, error: mutation.error, submit: mutation.run, reset: mutation.reset };
}

export function useFeedbackItems(filters = {}) {
  const query = useDomainQuery({
    queryKey: ["feedback-items", filters.destination ?? "", filters.datasetId ?? "", filters.limit ?? 0],
    queryFn: () => listFeedbackItems(filters),
    emptyValue: [],
  });

  return { feedbackItems: query.data, ...withoutData(query) };
}

function withoutData({ data: _data, ...query }) {
  return query;
}
