package postgres

import "context"

// GetDatasetVersion returns bounded metadata, never the full sample manifest,
// archive descriptors or host paths.
func (m *ReadModels) GetDatasetVersion(ctx context.Context, id string) (map[string]any, error) {
	return queryObject(ctx, m.pool, `SELECT jsonb_build_object(
 'dataset_id',d.id,'dataset_key',d.dataset_key,'dataset_name',d.name,
 'dataset_version_id',v.id,'version_key',v.version_key,'version_number',v.version_number,
 'parent_version_id',v.parent_version_id,'source_type',v.source_type,
 'sample_count',v.sample_count,'class_count',v.class_count,
 'split_counts',v.split_summary,'readiness',v.readiness_report,
 'classes',COALESCE(a.artifact_metadata->'manifest'->'classes','[]'::jsonb),
 'change_summary',v.change_summary - 'sample_sources','created_at',v.created_at
 ) FROM dataset_versions v JOIN datasets d ON d.id=v.dataset_id
 LEFT JOIN artifacts a ON a.id=v.manifest_artifact_id
 WHERE v.id::text=$1 OR v.version_key=$1`, id)
}
