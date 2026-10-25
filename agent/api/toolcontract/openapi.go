package toolcontract

import "strings"

// OpenAPI is generated from the same schemas enforced by both sides. These are
// platform-owned tool bridge endpoints, not the future Agent conversation API.
func OpenAPI() map[string]any {
	result := map[string]any{"type": "object", "required": []string{"tool", "data", "observed_at", "evidence"}, "properties": map[string]any{
		"tool": map[string]any{"type": "string"}, "data": map[string]any{"type": "object", "additionalProperties": true},
		"observed_at": map[string]any{"type": "string", "format": "date-time"}, "evidence": map[string]any{"type": "array", "items": map[string]any{"type": "object", "additionalProperties": true}}, "operation_id": map[string]any{"type": "string"},
	}}
	responses := func(success string) map[string]any {
		return map[string]any{success: map[string]any{"description": "Tool response or approval", "content": map[string]any{"application/json": map[string]any{"schema": result}}}, "default": map[string]any{"description": "Structured error, including unknown write outcomes", "content": map[string]any{"application/json": map[string]any{"schema": map[string]any{"type": "object", "additionalProperties": true}}}}}
	}
	paths := map[string]any{}
	security := []map[string][]string{{"LocalSession": {}}, {"SecureSession": {}}}
	for _, d := range Definitions() {
		props := map[string]any{"arguments": d.InputSchema}
		required := []string{"arguments"}
		if !d.ReadOnly {
			props["operation_id"] = map[string]any{"type": "string", "format": "uuid"}
			props["approval"] = map[string]any{"type": "string", "minLength": 1}
			required = append(required, "operation_id", "approval")
		}
		operation := map[string]any{"tags": []string{"Agent 工具"}, "summary": d.Description, "operationId": "invoke_" + strings.ReplaceAll(d.Name, ".", "_"), "security": security,
			"parameters":  []any{map[string]any{"name": "X-CSRF-Token", "in": "header", "required": true, "schema": map[string]any{"type": "string"}}},
			"requestBody": map[string]any{"required": true, "content": map[string]any{"application/json": map[string]any{"schema": map[string]any{"type": "object", "additionalProperties": false, "properties": props, "required": required}}}}, "responses": responses("200"),
		}
		if d.Name == "training.submit_plan" {
			operation["responses"] = responses("202")
		}
		paths["/api/agent-tools/invoke/"+d.Name] = map[string]any{"post": operation}
		if !d.ReadOnly {
			paths["/api/agent-tools/approvals/"+d.Name] = map[string]any{"post": map[string]any{"tags": []string{"用户确认"}, "summary": "Explicit user confirmation for " + d.Name, "operationId": "approve_" + strings.ReplaceAll(d.Name, ".", "_"), "security": security,
				"parameters": operation["parameters"], "requestBody": map[string]any{"required": true, "content": map[string]any{"application/json": map[string]any{"schema": map[string]any{"type": "object", "additionalProperties": false, "required": []string{"arguments", "operation_id"}, "properties": map[string]any{"arguments": d.InputSchema, "operation_id": map[string]any{"type": "string", "format": "uuid"}}}}}},
				"responses": map[string]any{"200": map[string]any{"description": "Approval bound to current user, session and exact normalized arguments", "content": map[string]any{"application/json": map[string]any{"schema": map[string]any{"type": "object", "additionalProperties": true}}}}, "default": map[string]any{"description": "Approval rejected"}},
			}}
		}
	}
	paths["/api/agent-tools/catalog"] = map[string]any{"get": map[string]any{"tags": []string{"Agent 工具"}, "summary": "当前用户可用工具", "operationId": "getAgentToolCatalog", "security": security, "responses": map[string]any{"200": map[string]any{"description": "Role-filtered tool definitions and schemas"}}}}
	paths["/api/agent-tools/operations/{id}"] = map[string]any{"get": map[string]any{"tags": []string{"操作对账"}, "summary": "读取本人操作并尝试找回已提交结果", "operationId": "getAgentToolOperation", "security": security, "parameters": []any{map[string]any{"name": "id", "in": "path", "required": true, "schema": map[string]any{"type": "string", "format": "uuid"}}}, "responses": map[string]any{"200": map[string]any{"description": "Durable recorded outcome or unresolved operation"}, "404": map[string]any{"description": "Operation absent or belongs to another user"}}}}
	return map[string]any{"openapi": "3.1.0", "info": map[string]any{"title": "FineVision Agent Tool Bridge", "version": "1.0.0"}, "servers": []any{map[string]string{"url": "/"}}, "paths": paths, "components": map[string]any{"securitySchemes": map[string]any{"LocalSession": map[string]any{"type": "apiKey", "in": "cookie", "name": "fv_session"}, "SecureSession": map[string]any{"type": "apiKey", "in": "cookie", "name": "__Host-fv_session"}}}}
}
