"""add review workflow metadata

Revision ID: 20260619_0003
Revises: 20260619_0002
Create Date: 2026-06-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260619_0003"
down_revision = "20260619_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inference_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_status", sa.Text(), nullable=False),
        sa.Column("model_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("feature_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("threshold_strategy_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("input_type", sa.Text(), nullable=False),
        sa.Column("input_ref", sa.Text()),
        sa.Column("sample_id", sa.Text()),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("margin", sa.Float()),
        sa.Column("ood_score", sa.Float()),
        sa.Column("reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column("result_payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("input_type in ('sample', 'image_path', 'upload')", name="ck_inference_events_input_type"),
        sa.CheckConstraint("decision in ('accept', 'abstain', 'reject_ood')", name="ck_inference_events_decision"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"]),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
        sa.ForeignKeyConstraint(["model_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["feature_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["threshold_strategy_artifact_id"], ["artifacts.id"]),
    )
    op.create_unique_constraint("uq_inference_events_event_key", "inference_events", ["event_key"])
    op.create_index("ix_inference_events_dataset_version_created_at", "inference_events", ["dataset_version_id", "created_at"])
    op.create_index("ix_inference_events_decision_created_at", "inference_events", ["decision", "created_at"])

    op.create_table(
        "review_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("review_key", sa.Text(), nullable=False),
        sa.Column("inference_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sample_id", sa.Text()),
        sa.Column("input_ref", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("risk_type", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("assistance_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("assigned_to", sa.Text()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("feedbacked_at", sa.DateTime(timezone=True)),
        sa.Column("completed_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'submitted', 'feedbacked', 'skipped', 'disputed')",
            name="ck_review_items_status",
        ),
        sa.CheckConstraint(
            "risk_type in ('low_confidence', 'low_margin', 'ood_candidate', 'mixed')",
            name="ck_review_items_risk_type",
        ),
        sa.ForeignKeyConstraint(["inference_event_id"], ["inference_events.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"]),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
    )
    op.create_unique_constraint("uq_review_items_review_key", "review_items", ["review_key"])
    op.create_unique_constraint("uq_review_items_inference_event_id", "review_items", ["inference_event_id"])
    op.create_index("ix_review_items_status_priority_created_at", "review_items", ["status", "priority", "created_at"])
    op.create_index("ix_review_items_dataset_version", "review_items", ["dataset_version_id", "created_at"])

    op.create_table(
        "feedback_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("feedback_key", sa.Text(), nullable=False),
        sa.Column("review_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inference_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sample_id", sa.Text()),
        sa.Column("final_label", sa.Text()),
        sa.Column("final_outcome", sa.Text(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("reviewer_note", sa.Text()),
        sa.Column("feedback_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "final_outcome in ('confirmed_label', 'corrected_label', 'ood', 'bad_image', 'uncertain', 'ignore')",
            name="ck_feedback_items_final_outcome",
        ),
        sa.CheckConstraint(
            "destination in ('training_candidate', 'ood_stress', 'bad_image', 'taxonomy_dispute', 'ignore')",
            name="ck_feedback_items_destination",
        ),
        sa.ForeignKeyConstraint(["review_item_id"], ["review_items.id"]),
        sa.ForeignKeyConstraint(["inference_event_id"], ["inference_events.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"]),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
    )
    op.create_unique_constraint("uq_feedback_items_feedback_key", "feedback_items", ["feedback_key"])
    op.create_unique_constraint("uq_feedback_items_review_item_id", "feedback_items", ["review_item_id"])
    op.create_index("ix_feedback_items_destination_created_at", "feedback_items", ["destination", "created_at"])
    op.create_index("ix_feedback_items_dataset_version", "feedback_items", ["dataset_version_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_feedback_items_dataset_version", table_name="feedback_items")
    op.drop_index("ix_feedback_items_destination_created_at", table_name="feedback_items")
    op.drop_constraint("uq_feedback_items_review_item_id", "feedback_items", type_="unique")
    op.drop_constraint("uq_feedback_items_feedback_key", "feedback_items", type_="unique")
    op.drop_table("feedback_items")

    op.drop_index("ix_review_items_dataset_version", table_name="review_items")
    op.drop_index("ix_review_items_status_priority_created_at", table_name="review_items")
    op.drop_constraint("uq_review_items_inference_event_id", "review_items", type_="unique")
    op.drop_constraint("uq_review_items_review_key", "review_items", type_="unique")
    op.drop_table("review_items")

    op.drop_index("ix_inference_events_decision_created_at", table_name="inference_events")
    op.drop_index("ix_inference_events_dataset_version_created_at", table_name="inference_events")
    op.drop_constraint("uq_inference_events_event_key", "inference_events", type_="unique")
    op.drop_table("inference_events")
