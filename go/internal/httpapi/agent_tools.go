package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/toolcontract"
	"github.com/tianyuqic1/FGVCplateformPublic/go/api/openapi"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/agenttools"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/auth"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/testerrors"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/training"
)

type TestErrorAnalyzer interface {
	Analyze(context.Context, string, int, int) (testerrors.Report, error)
}
type AgentToolOptions struct {
	TestErrors TestErrorAnalyzer
	SigningKey []byte
	Operations agenttools.Store
	Now        func() time.Time
}
type agentToolRequest struct {
	Arguments   json.RawMessage `json:"arguments"`
	OperationID string          `json:"operation_id,omitempty"`
	Approval    string          `json:"approval,omitempty"`
}
type agentToolBridge struct {
	server      *Server
	options     AgentToolOptions
	target      func() http.Handler
	definitions map[string]toolcontract.Definition
}

func registerAgentTools(r chi.Router, server *Server, options AgentToolOptions, target func() http.Handler) {
	if options.Now == nil {
		options.Now = time.Now
	}
	b := &agentToolBridge{server: server, options: options, target: target, definitions: map[string]toolcontract.Definition{}}
	for _, d := range toolcontract.Definitions() {
		b.definitions[d.Name] = d
	}
	r.Get("/api/agent-tools/catalog", func(w http.ResponseWriter, r *http.Request) {
		user, ok := auth.UserFromContext(r.Context())
		if !ok {
			toolError(w, 401, "UNAUTHENTICATED", "login required", false)
			return
		}
		items := []toolcontract.Definition{}
		for _, d := range toolcontract.Definitions() {
			if toolAllowed(user.Role, d.Name) {
				items = append(items, d)
			}
		}
		reviewResponse(w, 200, map[string]any{"tools": items, "role": user.Role})
	})
	r.Post("/api/agent-tools/approvals/{tool}", b.approve)
	r.Post("/api/agent-tools/invoke/{tool}", b.invoke)
	r.Get("/api/agent-tools/operations/{id}", b.operation)
}

func toolAllowed(role auth.Role, name string) bool {
	return auth.AgentToolAllowed(role, name)
}
func toolError(w http.ResponseWriter, status int, code, message string, unknown bool) {
	reviewResponse(w, status, map[string]any{"error": map[string]any{"code": code, "message": message, "unknown": unknown}})
}
func (b *agentToolBridge) decode(w http.ResponseWriter, r *http.Request) (agentToolRequest, toolcontract.Definition, auth.User, bool) {
	w.Header().Set("Cache-Control", "no-store")
	var in agentToolRequest
	name := chi.URLParam(r, "tool")
	definition, exists := b.definitions[name]
	if !exists {
		toolError(w, 404, "UNKNOWN_TOOL", "tool is not registered", false)
		return in, definition, auth.User{}, false
	}
	user, ok := auth.UserFromContext(r.Context())
	if !ok || !toolAllowed(user.Role, name) {
		toolError(w, 403, "FORBIDDEN", "tool is unavailable for this user", false)
		return in, definition, user, false
	}
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 96<<10))
	d.DisallowUnknownFields()
	if d.Decode(&in) != nil || d.Decode(new(any)) != io.EOF {
		toolError(w, 422, "INVALID_ARGUMENTS", "expected one tool request object", false)
		return in, definition, user, false
	}
	if err := definition.InputSchema.Validate(in.Arguments); err != nil {
		toolError(w, 422, "INVALID_ARGUMENTS", err.Error(), false)
		return in, definition, user, false
	}
	if !definition.ReadOnly {
		id, err := uuid.Parse(in.OperationID)
		if err != nil || id == uuid.Nil {
			toolError(w, 422, "INVALID_OPERATION_ID", "write operation ID must be a UUID", false)
			return in, definition, user, false
		}
		in.OperationID = id.String()
	}
	return in, definition, user, true
}

func (b *agentToolBridge) approve(w http.ResponseWriter, r *http.Request) {
	in, d, user, ok := b.decode(w, r)
	if !ok {
		return
	}
	if d.ReadOnly {
		toolError(w, 422, "READ_ONLY_TOOL", "read tools do not need approval", false)
		return
	}
	if len(b.options.SigningKey) < 32 || b.options.Operations == nil {
		toolError(w, 503, "WRITES_NOT_CONFIGURED", "approval key and durable operation store are required", false)
		return
	}
	binding := auth.SessionBinding(r.Context())
	if binding == "" {
		toolError(w, 401, "UNAUTHENTICATED", "authenticated session required", false)
		return
	}
	// Return the exact, fully normalized training parameters the UI must show.
	// The resulting receipt cannot approve the original underspecified input.
	if d.Name == "training.submit_plan" {
		_, normalized, err := b.prepare(r.Context(), in.Arguments)
		if err != nil {
			toolError(w, 422, "INVALID_TRAINING_PLAN", err.Error(), false)
			return
		}
		in.Arguments = normalized
	}
	digest, _ := agenttools.Digest(in.Arguments)
	expires := b.options.Now().Add(15 * time.Minute)
	token, err := agenttools.Sign(b.options.SigningKey, agenttools.Receipt{Actor: user.ID.String(), Session: binding, Tool: d.Name, Operation: in.OperationID, Digest: digest, Expires: expires.Unix()})
	if err != nil {
		toolError(w, 503, "APPROVAL_UNAVAILABLE", "approval is unavailable", false)
		return
	}
	reviewResponse(w, 200, map[string]any{"approval": token, "arguments": in.Arguments, "arguments_sha256": digest, "operation_id": in.OperationID, "expires_at": expires.UTC()})
}

func (b *agentToolBridge) invoke(w http.ResponseWriter, r *http.Request) {
	in, d, user, ok := b.decode(w, r)
	if !ok {
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(d.TimeoutSeconds)*time.Second)
	defer cancel()
	r = r.WithContext(ctx)
	if d.ReadOnly {
		status, body := b.execute(r, d.Name, in.Arguments, "")
		b.respond(w, d.Name, in, body, status)
		return
	}
	if b.options.Operations == nil {
		toolError(w, 503, "WRITES_NOT_CONFIGURED", "durable operation store required", false)
		return
	}
	digest, _ := agenttools.Digest(in.Arguments)
	expected := agenttools.Receipt{Actor: user.ID.String(), Session: auth.SessionBinding(ctx), Tool: d.Name, Operation: in.OperationID, Digest: digest}
	if expected.Session == "" || agenttools.Verify(b.options.SigningKey, in.Approval, expected, b.options.Now()) != nil {
		toolError(w, 403, "CONFIRMATION_REQUIRED", "approval must match current user, session, operation and arguments", false)
		return
	}
	op, fresh, err := b.options.Operations.Begin(ctx, agenttools.Operation{ID: in.OperationID, Actor: user.ID.String(), Tool: d.Name, Digest: digest})
	if errors.Is(err, agenttools.ErrConflict) {
		toolError(w, 409, "IDEMPOTENCY_CONFLICT", err.Error(), false)
		return
	}
	if err != nil {
		toolError(w, 503, "OPERATION_STORE_UNAVAILABLE", "cannot durably register action", false)
		return
	}
	if !fresh {
		if op.Status == "completed" && op.HTTPStatus < 500 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(op.HTTPStatus)
			_, _ = w.Write(op.Response)
			return
		}
		if recoveredStatus, recovered, ok := b.recover(ctx, op); ok {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(recoveredStatus)
			_, _ = w.Write(recovered)
			return
		}
		if op.Status == "completed" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(op.HTTPStatus)
			_, _ = w.Write(op.Response)
			return
		}
		toolError(w, 409, "OPERATION_PENDING", "operation is in progress or its outcome is unknown; query its status before retrying", true)
		return
	}
	status, body := b.execute(r, d.Name, in.Arguments, user.ID.String()+":"+in.OperationID)
	encoded := b.envelope(d.Name, in, body, status)
	// Request cancellation must not discard an already completed outcome.
	finishCtx, finishCancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer finishCancel()
	if err := b.options.Operations.Finish(finishCtx, user.ID.String(), in.OperationID, status, encoded); err != nil {
		toolError(w, 503, "OPERATION_OUTCOME_UNKNOWN", "action may have completed; inspect operation and business state", true)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(encoded)
}

func (b *agentToolBridge) operation(w http.ResponseWriter, r *http.Request) {
	user, ok := auth.UserFromContext(r.Context())
	if !ok {
		toolError(w, 401, "UNAUTHENTICATED", "login required", false)
		return
	}
	id, parseErr := uuid.Parse(chi.URLParam(r, "id"))
	if parseErr != nil || id == uuid.Nil {
		toolError(w, 422, "INVALID_OPERATION_ID", "invalid UUID", false)
		return
	}
	if b.options.Operations == nil {
		toolError(w, 503, "OPERATION_STORE_UNAVAILABLE", "operation store unavailable", false)
		return
	}
	op, err := b.options.Operations.Get(r.Context(), user.ID.String(), id.String())
	if errors.Is(err, agenttools.ErrNotFound) {
		toolError(w, 404, "NOT_FOUND", "operation not found", false)
		return
	}
	if err != nil {
		toolError(w, 503, "OPERATION_STORE_UNAVAILABLE", "cannot query operation", false)
		return
	}
	if op.Status == "running" || op.HTTPStatus >= 500 {
		if _, _, ok := b.recover(r.Context(), op); ok {
			var readErr error
			op, readErr = b.options.Operations.Get(r.Context(), user.ID.String(), op.ID)
			if readErr != nil {
				toolError(w, 503, "OPERATION_STORE_UNAVAILABLE", "cannot read recovered operation", false)
				return
			}
		}
	}
	reviewResponse(w, 200, op)
}

func (b *agentToolBridge) recover(ctx context.Context, op agenttools.Operation) (int, json.RawMessage, bool) {
	resolver, ok := b.options.Operations.(agenttools.BusinessResolver)
	if !ok {
		return 0, nil, false
	}
	data, found, err := resolver.Recover(ctx, op)
	if err != nil || !found {
		return 0, nil, false
	}
	status := 200
	if op.Tool == "training.submit_plan" {
		status = 202
	}
	body := b.envelope(op.Tool, agentToolRequest{Arguments: json.RawMessage(`{}`), OperationID: op.ID}, data, status)
	if err := b.options.Operations.Finish(ctx, op.Actor, op.ID, status, body); err != nil {
		return 0, nil, false
	}
	return status, body, true
}

func (b *agentToolBridge) prepare(ctx context.Context, raw json.RawMessage) (training.CreateCommand, json.RawMessage, error) {
	var body openapi.CreateTrainingRun
	if err := json.Unmarshal(raw, &body); err != nil {
		return training.CreateCommand{}, nil, err
	}
	command, err := b.server.prepareTrainingRun(ctx, &body)
	if err != nil {
		return command, nil, err
	}
	var normalized map[string]any
	_ = json.Unmarshal(raw, &normalized)
	normalized["dataset_version_id"] = command.DatasetVersionID
	normalized["backbone_key"] = command.BackboneID
	normalized["feature_batch_size"] = command.Payload["batch_size"]
	normalized["image_size"] = command.Payload["image_size"]
	normalized["max_attempts"] = command.MaxAttempts
	head := map[string]any{}
	if config, ok := command.Payload["head_config"].(map[string]any); ok {
		for key, v := range config {
			if key != "head_type" && key != "training_mode" && key != "train_backbone" {
				head[key] = v
			}
		}
	}
	normalized["head_config"] = head
	encoded, err := json.Marshal(normalized)
	return command, encoded, err
}

func (b *agentToolBridge) execute(r *http.Request, name string, raw json.RawMessage, operationKey string) (int, json.RawMessage) {
	var args map[string]any
	_ = json.Unmarshal(raw, &args)
	fail := func(code int, message string) (int, json.RawMessage) {
		body, _ := json.Marshal(map[string]any{"error": map[string]string{"code": "TOOL_REJECTED", "message": message}})
		return code, body
	}
	if name == "training.analyze_test_errors" {
		if b.options.TestErrors == nil {
			return fail(503, "test error analysis unavailable")
		}
		pairs, examples := 10, 10
		if v, ok := args["max_confusion_pairs"].(float64); ok {
			pairs = int(v)
		}
		if v, ok := args["max_examples"].(float64); ok {
			examples = int(v)
		}
		report, err := b.options.TestErrors.Analyze(r.Context(), args["run_id"].(string), pairs, examples)
		if errors.Is(err, testerrors.ErrNotFound) {
			return fail(404, "training run not found")
		}
		if err != nil {
			return fail(503, "cannot verify or read training test report")
		}
		raw, _ := json.Marshal(report)
		return 200, raw
	}
	if name == "training.cancel_run" {
		runID := args["run_id"].(string)
		jobID, err := b.server.jobIDForRun(r.Context(), runID)
		if err != nil {
			status := 503
			if errors.Is(err, ErrReadModelNotFound) || training.ErrorCode(err) == training.CodeNotFound {
				status = 404
			}
			return fail(status, "training status unavailable or missing")
		}
		if err := b.server.lifecycle.CancelOperation(r.Context(), jobID, operationKey); err != nil {
			status := trainingToolStatus(err)
			if status >= 500 {
				return fail(status, "cancellation could not be confirmed; inspect operation status")
			}
			return fail(status, err.Error())
		}
		body, _ := json.Marshal(map[string]any{"training_run": map[string]any{"id": runID, "run_id": runID, "job_id": jobID, "status": "cancelled"}})
		return 200, body
	}
	if name == "training.validate_plan" || name == "training.submit_plan" {
		command, normalized, err := b.prepare(r.Context(), raw)
		if err != nil {
			return fail(422, err.Error())
		}
		if name == "training.validate_plan" {
			digest, _ := agenttools.Digest(normalized)
			data, _ := json.Marshal(map[string]any{"valid": true, "normalized_arguments": normalized, "arguments_sha256": digest, "dataset_version_id": command.DatasetVersionID})
			return 200, data
		}
		command.OperationKey = operationKey
		model, err := b.server.createPreparedTraining(r.Context(), command)
		if err != nil {
			return fail(trainingToolStatus(err), "training could not be confirmed; inspect operation status")
		}
		data, _ := json.Marshal(map[string]any{"training_run": model})
		return 202, data
	}
	if name == "datasets.get_version" {
		if reader, ok := b.server.reads.(interface {
			GetDatasetVersion(context.Context, string) (map[string]any, error)
		}); ok {
			data, err := reader.GetDatasetVersion(r.Context(), args["dataset_version_id"].(string))
			if errors.Is(err, ErrReadModelNotFound) {
				return fail(404, "dataset version not found")
			}
			if err != nil {
				return fail(503, "dataset version unavailable")
			}
			encoded, _ := json.Marshal(data)
			return 200, encoded
		}
		return fail(503, "dataset version reader unavailable")
	}
	path, method, body, err := toolRoute(name, args, raw)
	if err != nil {
		return fail(422, err.Error())
	}
	callContext := auth.ToolDispatch(context.WithValue(r.Context(), chi.RouteCtxKey, chi.NewRouteContext()))
	if operationKey != "" {
		callContext = agenttools.WithOperation(callContext, operationKey)
	}
	request := r.Clone(callContext)
	u, _ := url.Parse(path)
	request.URL = u
	request.Method = method
	request.RequestURI = u.RequestURI()
	request.Body = io.NopCloser(bytes.NewReader(body))
	request.ContentLength = int64(len(body))
	request.Header = request.Header.Clone()
	request.Header.Set("Content-Type", "application/json")
	response := &toolResponse{header: make(http.Header)}
	b.target().ServeHTTP(response, request)
	if response.overflow {
		return fail(422, "tool response exceeds 512 KiB; narrow the query")
	}
	if response.status == 0 {
		response.status = 200
	}
	if !json.Valid(response.body.Bytes()) {
		return fail(502, "platform returned invalid JSON")
	}
	return response.status, response.body.Bytes()
}

func trainingToolStatus(err error) int {
	switch training.ErrorCode(err) {
	case training.CodeValidationFailed:
		return 422
	case training.CodeIdempotencyConflict, training.CodeInvalidState, training.CodeFenced:
		return 409
	case training.CodeNotFound:
		return 404
	default:
		return 503
	}
}

func toolRoute(name string, args map[string]any, raw json.RawMessage) (string, string, []byte, error) {
	query := url.Values{}
	for k, v := range args {
		switch value := v.(type) {
		case string:
			query.Set(k, value)
		case float64:
			query.Set(k, strconv.FormatFloat(value, 'f', -1, 64))
		}
	}
	page := func(path string) (string, string, []byte, error) {
		if query.Get("limit") == "" {
			query.Set("limit", "20")
		}
		return path + "?" + query.Encode(), http.MethodGet, nil, nil
	}
	id := func(key string) string { return url.PathEscape(args[key].(string)) }
	switch name {
	case "datasets.list":
		return page("/api/datasets")
	case "training.list_runs":
		return page("/api/training-runs")
	case "training.get_run":
		return "/api/training-runs/" + id("run_id"), http.MethodGet, nil, nil
	case "training.get_metrics":
		query.Del("run_id")
		if query.Get("limit") == "" {
			query.Set("limit", "100")
		}
		return "/api/training-runs/" + id("run_id") + "/metrics?" + query.Encode(), http.MethodGet, nil, nil
	case "models.list_versions":
		return page("/api/model-versions")
	case "models.get_version":
		return "/api/model-versions/" + id("model_version_id"), http.MethodGet, nil, nil
	case "models.compare":
		return "/api/model-version-comparisons", http.MethodPost, raw, nil
	case "catalog.list_backbones":
		return "/api/model-weights", http.MethodGet, nil, nil
	case "workers.get_capacity":
		return page("/api/workers")
	case "training.cancel_run":
		return "/api/training-runs/" + id("run_id") + "/cancel", http.MethodPost, []byte("{}"), nil
	case "inference.run":
		return "/api/inference", http.MethodPost, raw, nil
	}
	return "", "", nil, fmt.Errorf("tool route unavailable")
}

type toolResponse struct {
	header   http.Header
	status   int
	body     bytes.Buffer
	overflow bool
}

func (w *toolResponse) Header() http.Header { return w.header }
func (w *toolResponse) WriteHeader(status int) {
	if w.status == 0 {
		w.status = status
	}
}
func (w *toolResponse) Write(data []byte) (int, error) {
	if w.status == 0 {
		w.status = 200
	}
	if w.body.Len()+len(data) > 512<<10 {
		w.overflow = true
		return 0, fmt.Errorf("tool response too large")
	}
	return w.body.Write(data)
}

func scrubToolData(v any) any {
	switch value := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for key, item := range value {
			switch strings.ToLower(key) {
			case "uri", "cache_dir", "local_path", "path", "authorization", "cookie", "csrf_token", "api_key", "password", "trace_context":
				continue
			}
			out[key] = scrubToolData(item)
		}
		return out
	case []any:
		for i := range value {
			value[i] = scrubToolData(value[i])
		}
		return value
	case string:
		if strings.HasPrefix(value, "s3://") || strings.HasPrefix(value, "file://") || strings.HasPrefix(value, "/home/") || strings.HasPrefix(value, "/data/") {
			return "[storage-reference]"
		}
	}
	return v
}
func (b *agentToolBridge) envelope(name string, in agentToolRequest, raw json.RawMessage, status int) json.RawMessage {
	if status >= 400 {
		var value map[string]any
		_ = json.Unmarshal(raw, &value)
		item, ok := value["error"].(map[string]any)
		if !ok {
			item = map[string]any{"code": "PLATFORM_REJECTED", "message": http.StatusText(status)}
			if message, ok := value["detail"].(string); ok {
				item["message"] = message
			}
		}
		item["unknown"] = status >= 500
		encoded, _ := json.Marshal(map[string]any{"error": item})
		return encoded
	}
	var data any
	_ = json.Unmarshal(raw, &data)
	now := b.options.Now().UTC()
	evidence := []map[string]any{}
	var args map[string]any
	_ = json.Unmarshal(in.Arguments, &args)
	for _, key := range []string{"dataset_version_id", "model_version_id", "run_id"} {
		if id, ok := args[key].(string); ok {
			evidence = append(evidence, map[string]any{"type": strings.TrimSuffix(key, "_id"), "id": id, "observed_at": now})
		}
	}
	result, _ := json.Marshal(map[string]any{"tool": name, "data": scrubToolData(data), "observed_at": now, "evidence": evidence, "operation_id": in.OperationID})
	return result
}
func (b *agentToolBridge) respond(w http.ResponseWriter, name string, in agentToolRequest, raw json.RawMessage, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_, _ = w.Write(b.envelope(name, in, raw, status))
}
