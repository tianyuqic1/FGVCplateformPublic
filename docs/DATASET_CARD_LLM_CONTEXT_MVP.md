# Dataset Card LLM Context MVP

## Objective

Improve LLM assistance quality by giving it a controlled, versioned dataset context instead of
letting it infer domain meaning from class names and raw model evidence alone.

This MVP adds a dataset card for each `dataset_version_id` and injects that card into inference and
review assistance prompts. The card is advisory context only. It does not change labels, thresholds,
dataset versions, model versions, feedback items, or review outcomes.

## Problem

Current LLM assistance sees top-k predictions, confidence, margin, OOD score, decision reasons, and
sometimes nearest-neighbor evidence. For generic classes such as CIFAR-10, that is not enough context
to produce grounded guidance. The LLM can produce technically plausible but awkward suggestions, such
as treating a simple benchmark image as a domain-specific production sample.

The platform needs a small, explicit description of what the dataset is, which classes are in scope,
what should be considered OOD, and how a human reviewer should reason about uncertain samples.

## MVP Scope

Add a version-level dataset card:

```text
dataset_version_id -> dataset_card
```

The initial card is generated deterministically during dataset import from the manifest:

- dataset id and version id
- task
- domain
- summary
- class list
- sample count and class count
- split summary
- readiness warnings
- known confusions
- OOD policy
- review guidance
- source and updated timestamp

Users can edit the card from the dataset detail page. LLM assistance receives the card from the
backend when the request references a dataset version or review item.

## Non-Goals

- Do not let the LLM create final labels.
- Do not let the LLM update the dataset card automatically.
- Do not write feedback directly into the training dataset.
- Do not implement online abstention updates.
- Do not add VLM image understanding in this iteration.
- Do not add a separate dataset-card governance workflow yet.

## Data Model

Store `dataset_card` on `dataset_versions` as JSONB.

Why version-level:

- Training, inference, review, and feedback all reference a dataset version.
- Historical LLM explanations must be reproducible against the context used at that time.
- Dataset-level cards can drift and misrepresent older review items.

MVP schema shape:

```json
{
  "schema_version": 1,
  "dataset_id": "cifar10-mini",
  "dataset_version_id": "dataset@cifar10-mini-001",
  "task": "image classification",
  "domain": "CIFAR-10 benchmark images",
  "summary": "Small 10-class image classification dataset.",
  "classes": ["airplane", "automobile", "bird"],
  "class_definitions": {
    "airplane": "Aircraft images in CIFAR-10 scope."
  },
  "known_confusions": [
    "cat <> dog: visually similar animal classes",
    "automobile <> truck: vehicle classes can overlap"
  ],
  "ood_policy": "Images outside the configured classes, unreadable images, or ambiguous inputs should be reviewed as OOD/uncertain.",
  "review_guidance": "Use the image content as the source of truth. Treat LLM output as advisory only.",
  "source": "imagefolder_import",
  "updated_at": "2026-06-20T00:00:00Z"
}
```

## API

Dataset detail should include the latest version card:

```text
GET /api/datasets/{dataset_id}
```

Dataset-card endpoints:

```text
GET /api/dataset-versions/{dataset_version_id}/card
PUT /api/dataset-versions/{dataset_version_id}/card
```

`PUT` accepts a JSON object and returns the normalized card. The backend should reject non-object or
oversized payloads and should preserve required fields such as `schema_version`,
`dataset_version_id`, and `updated_at`.

LLM endpoints:

```text
POST /api/llm/assist
POST /api/review-items/{review_item_id}/assist
```

The backend injects `dataset_card` into the LLM context when it can resolve a
`dataset_version_id`. The frontend may show a context preview, but card resolution should not depend
on the frontend manually passing the card.

## Frontend

Add a `Dataset Card` tab to the dataset detail page.

Recommended fields:

- task
- domain
- summary
- class definitions
- known confusions
- OOD policy
- review guidance

The page should show:

- an explicit `used as LLM context` status chip
- an `advisory only` reminder
- a save state: idle, saving, saved, error
- a read-only preview of the JSON context sent to LLM assistance

Classes should be displayed from the manifest and not edited in this MVP.

## Prompt Contract

When a dataset card exists, LLM assistance should:

- ground all suggestions in `dataset_card`, model evidence, and review context
- avoid inventing new classes or production policies not present in the card
- explain uncertainty using the dataset's actual class scope
- keep output advisory-only
- avoid recommending direct threshold changes from a single sample

## Acceptance

- Imported datasets receive a deterministic dataset card.
- Dataset detail returns the latest version card.
- The dataset card can be edited and read back.
- Review assistance receives a card matching the review item's `dataset_version_id`.
- Generic assistance receives a card when `dataset_version_id` is present in context.
- LLM output remains advisory-only and does not mutate labels, review status, feedback, thresholds,
  dataset versions, or model versions.
- Tests cover import, GET/PUT card, LLM context injection, and frontend client normalization.

## Implementation Order

1. Add `dataset_versions.dataset_card` and migration.
2. Generate an initial card during dataset import.
3. Add store methods for read/update card in DB-backed and file-backed modes.
4. Add card API endpoints.
5. Inject cards into LLM assistance contexts.
6. Add dataset detail `Dataset Card` tab.
7. Update tests and smoke checks.
8. Re-run backend tests, frontend smoke/build, and `docker compose config`.

## Risks

- If the card is too long, it can crowd out top-k and review evidence. Keep it compact.
- If it contains paths or sample-level details, it may leak internal data. Keep it manifest-level.
- If it is dataset-level rather than version-level, historical explanations can drift.
- If users think the LLM is authoritative, review quality can suffer. Keep advisory-only UI copy.
