package postgres

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/agenttools"
)

type AgentToolOperations struct{ Pool *pgxpool.Pool }

func (s *AgentToolOperations) Begin(ctx context.Context, op agenttools.Operation) (agenttools.Operation, bool, error) {
	r, err := s.Pool.Exec(ctx, `INSERT INTO agent_tool_operations(actor_id,operation_id,tool,arguments_sha256,status) VALUES($1,$2,$3,$4,'running') ON CONFLICT(actor_id,operation_id) DO NOTHING`, op.Actor, op.ID, op.Tool, op.Digest)
	if err != nil {
		return agenttools.Operation{}, false, err
	}
	old, err := s.Get(ctx, op.Actor, op.ID)
	if err != nil {
		return old, false, err
	}
	if old.Tool != op.Tool || old.Digest != op.Digest {
		return agenttools.Operation{}, false, agenttools.ErrConflict
	}
	return old, r.RowsAffected() == 1, nil
}
func (s *AgentToolOperations) Finish(ctx context.Context, actor, id string, status int, body json.RawMessage) error {
	r, err := s.Pool.Exec(ctx, `UPDATE agent_tool_operations SET status='completed',http_status=$3,response=$4,updated_at=now() WHERE actor_id=$1 AND operation_id=$2 AND (status='running' OR (status='completed' AND http_status>=500))`, actor, id, status, []byte(body))
	if err != nil {
		return err
	}
	if r.RowsAffected() != 1 {
		return agenttools.ErrConflict
	}
	return nil
}
func (s *AgentToolOperations) Get(ctx context.Context, actor, id string) (agenttools.Operation, error) {
	op := agenttools.Operation{Actor: actor, ID: id}
	err := s.Pool.QueryRow(ctx, `SELECT tool,arguments_sha256,status,http_status,response,updated_at FROM agent_tool_operations WHERE actor_id=$1 AND operation_id=$2`, actor, id).Scan(&op.Tool, &op.Digest, &op.Status, &op.HTTPStatus, &op.Response, &op.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return op, agenttools.ErrNotFound
	}
	op.Outcome = agenttools.Outcome(op.HTTPStatus)
	return op, err
}

func (s *AgentToolOperations) Recover(ctx context.Context, op agenttools.Operation) (json.RawMessage, bool, error) {
	key := op.Actor + ":" + op.ID
	var query string
	switch op.Tool {
	case "training.submit_plan":
		query = `SELECT jsonb_build_object('training_run',jsonb_build_object(
 'id',tr.id,'run_id',tr.id,'job_id',j.id,'status',tr.status,'name',j.payload->'name',
 'dataset_id',j.payload->'dataset_id','dataset_version_id',tr.dataset_version_id,
 'backbone_id',tr.backbone_id,'head_config',tr.head_config,'extractor_config',tr.extractor_config))
 FROM jobs j JOIN training_runs tr ON tr.job_id=j.id WHERE j.payload->>'agent_operation_key'=$1`
	case "inference.run":
		query = `SELECT jsonb_build_object('inference_result',result_payload) FROM inference_events WHERE request_payload->>'agent_operation_key'=$1`
	case "training.cancel_run":
		query = `SELECT jsonb_build_object('training_run',jsonb_build_object('id',tr.id,'run_id',tr.id,'job_id',j.id,'status',tr.status))
 FROM job_events e JOIN jobs j ON j.id=e.job_id JOIN training_runs tr ON tr.job_id=j.id WHERE e.payload->>'agent_operation_key'=$1 AND tr.status='cancelled' LIMIT 1`
	default:
		return nil, false, nil
	}
	var raw json.RawMessage
	err := s.Pool.QueryRow(ctx, query, key).Scan(&raw)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, false, nil
	}
	return raw, err == nil, err
}
