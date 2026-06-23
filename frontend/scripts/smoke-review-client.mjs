import {
  extractReviewItem,
  extractReviewItemList,
  listFeedbackItems,
  listReviewItems,
  normalizeFeedbackItem,
  normalizeReviewItem,
} from "../src/api/reviews.js";

const payload = {
  review_items: [
    {
      review_item_id: "review-001",
      inference_event_id: "inference-001",
      inference_run_id: "infer-run-001",
      batch_id: "infer-run-001",
      dataset_id: "toy",
      dataset_version_id: "dataset@toy-001",
      model_version_id: "toy-run-001-candidate",
      status: "pending",
      risk_type: "low_margin",
      priority: 60,
      reason: "Top-1 and top-2 scores are too close.",
      reason_codes: ["top1_top2_margin_below_threshold"],
      input_ref: "/data/uploads/query-001.jpg",
      image_url: "/api/uploads/query-001.jpg",
      assistance_metadata: {
        llm_assistance: {
          advisory_only: true,
          summary: "Inspect the top two candidates manually.",
          suggested_actions: ["Do not auto-submit."],
        },
      },
      context: {
        input: { sample_id: "sample-001", uploaded_image_path: "/data/uploads/query-001.jpg" },
        top_k: [
          { label: "red_square", score: 0.52 },
          { label: "green_circle", score: 0.49 },
        ],
        decision: {
          decision: "abstain",
          reasons: ["top1_top2_margin_below_threshold"],
          confidence: 0.52,
          margin: 0.03,
          ood_score: 0.2,
        },
        nearest_neighbors: [{ sample_id: "sample-002", label: "red_square", distance: 0.12 }],
      },
      feedback: null,
    },
  ],
};

const items = extractReviewItemList(payload).map(normalizeReviewItem);
if (items.length !== 1) throw new Error("Review list extraction failed");
if (items[0].id !== "review-001") throw new Error("Review id missing");
if (items[0].inferenceRunId !== "infer-run-001") throw new Error("Review inference run id missing");
if (items[0].batchId !== "infer-run-001") throw new Error("Review batch id missing");
if (items[0].riskType !== "low_margin") throw new Error("Risk type missing");
if (items[0].topK[0].label !== "red_square") throw new Error("Top-k not normalized");
if (items[0].decision.value !== "abstain") throw new Error("Decision not normalized");
if (items[0].nearestNeighbors[0].sampleId !== "sample-002") throw new Error("Neighbor id missing");
if (!items[0].imageUrl.endsWith("/api/uploads/query-001.jpg")) throw new Error("Review image url missing");
if (items[0].assistanceMetadata.llm_assistance.summary !== "Inspect the top two candidates manually.") {
  throw new Error("Assistance metadata missing");
}

const detail = normalizeReviewItem(extractReviewItem({ review_item: { ...payload.review_items[0], status: "feedbacked" } }));
if (detail.status !== "feedbacked") throw new Error("Detail status not normalized");

const completed = normalizeReviewItem(
  extractReviewItem({
    review_item: {
      ...payload.review_items[0],
      feedback: {
        feedback_item_id: "feedback-001",
        final_outcome: "corrected_label",
        destination: "training_candidate",
        final_label: "red_square",
      },
    },
  }),
);
if (completed.feedback.destination !== "training_candidate") throw new Error("Feedback destination missing");

const feedback = normalizeFeedbackItem({
  feedback_item_id: "feedback-001",
  review_item_id: "review-001",
  inference_event_id: "inference-001",
  inference_run_id: "infer-run-001",
  dataset_id: "toy",
  dataset_version_id: "dataset@toy-001",
  model_version_id: "toy-run-001-candidate",
  input_ref: "/data/uploads/query-001.jpg",
  image_url: "/api/uploads/query-001.jpg",
  final_outcome: "corrected_label",
  destination: "training_candidate",
  final_label: "red_square",
});
if (feedback.id !== "feedback-001") throw new Error("Feedback id missing");
if (feedback.reviewItemId !== "review-001") throw new Error("Feedback review id missing");
if (feedback.inferenceRunId !== "infer-run-001") throw new Error("Feedback inference run id missing");
if (feedback.datasetId !== "toy") throw new Error("Feedback dataset missing");
if (!feedback.imageUrl.endsWith("/api/uploads/query-001.jpg")) throw new Error("Feedback image url missing");

let requestedUrl = "";
globalThis.window = {
  setTimeout,
  clearTimeout,
};
globalThis.fetch = async (url) => {
  requestedUrl = url;
  return {
    ok: true,
    json: async () => payload,
  };
};

await listReviewItems({ status: "all", datasetId: "toy", limit: 80 });
if (!requestedUrl.includes("/api/review-items?")) throw new Error("Review list endpoint missing");
if (!requestedUrl.includes("status=all")) throw new Error("Review status filter missing");
if (!requestedUrl.includes("dataset_id=toy")) throw new Error("Review dataset filter missing");
if (!requestedUrl.includes("limit=80")) throw new Error("Review limit missing");

await listFeedbackItems({ destination: "training_candidate", datasetId: "toy", limit: 120 });
if (!requestedUrl.includes("/api/feedback-items?")) throw new Error("Feedback list endpoint missing");
if (!requestedUrl.includes("destination=training_candidate")) throw new Error("Feedback destination filter missing");
if (!requestedUrl.includes("dataset_id=toy")) throw new Error("Feedback dataset filter missing");
if (!requestedUrl.includes("limit=120")) throw new Error("Feedback limit missing");

console.log("review api client smoke passed");
