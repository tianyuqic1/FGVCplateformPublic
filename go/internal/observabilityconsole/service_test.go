package observabilityconsole

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestLogsUseBoundedClassificationAndNormalizeEntries(t *testing.T) {
	var receivedQuery string
	backend := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		receivedQuery = request.URL.Query().Get("query")
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"status":"success","data":{"resultType":"streams","result":[{"stream":{"service":"python-training-worker"},"values":[["1770000000000000000","{\"level\":\"ERROR\",\"service\":\"python-training-worker\",\"event\":\"training_attempt_failed\",\"message\":\"attempt failed\",\"job_id\":\"job-1\",\"trace_id\":\"4bf92f3577b34da6a3ce929d0e0e4736\"}"]]}]}}`))
	}))
	t.Cleanup(backend.Close)
	service := New(nil, Config{LokiURL: backend.URL, PrometheusURL: backend.URL, TempoURL: backend.URL})
	page, err := service.Logs(context.Background(), LogFilter{Category: "training", Level: "error", Search: `job-1" | drop`, Range: time.Hour, Limit: 20})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(receivedQuery, `service=~"python-training-worker" or compose_service=~"python-training-worker"`) || !strings.Contains(receivedQuery, `^ERROR[: ]`) {
		t.Fatalf("classification query = %s", receivedQuery)
	}
	if strings.Contains(receivedQuery, `| drop`) && !strings.Contains(receivedQuery, `\| drop`) {
		t.Fatalf("search escaped the LogQL pipeline: %s", receivedQuery)
	}
	if len(page.Items) != 1 || page.Items[0].Category != "training" || page.Items[0].Event != "training_attempt_failed" {
		t.Fatalf("page = %#v", page)
	}
	if page.Items[0].Fields["job_id"] != "job-1" || page.Items[0].Message != "attempt failed" {
		t.Fatalf("normalized item = %#v", page.Items[0])
	}
}

func TestUnstructuredLogSeverityIsInferredFromMessage(t *testing.T) {
	entry := parseLogEntry(1770000000000000000, "ERROR:worker:connection failed", map[string]string{"service": "deployment-worker", "level": "info"})
	if entry.Level != "ERROR" {
		t.Fatalf("level = %s", entry.Level)
	}
	warning := parseLogEntry(1770000000000000000, "2026-09-20 WARNING retrying", map[string]string{"service": "hardware-collector"})
	if warning.Level != "WARN" {
		t.Fatalf("warning level = %s", warning.Level)
	}
}

func TestUnifiedHTTPAPIReadsMetricsAlertsAndTraces(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		switch {
		case request.URL.Path == "/ready" || request.URL.Path == "/-/ready":
			_, _ = writer.Write([]byte(`{"status":"ready"}`))
		case request.URL.Path == "/api/v1/query":
			_, _ = writer.Write([]byte(`{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1770000000,"1"]}]}}`))
		case request.URL.Path == "/api/v1/alerts":
			_, _ = writer.Write([]byte(`{"status":"success","data":{"alerts":[{"labels":{"alertname":"FineVisionRuntimeTargetDown","severity":"critical"},"annotations":{"summary":"运行模块不可达"},"state":"firing","activeAt":"2026-09-20T00:00:00Z","value":"1"}]}}`))
		case strings.HasPrefix(request.URL.Path, "/api/v2/traces/"):
			_, _ = writer.Write([]byte(`{"trace":{"resourceSpans":[]}}`))
		default:
			http.NotFound(writer, request)
		}
	}))
	t.Cleanup(backend.Close)
	service := New(nil, Config{LokiURL: backend.URL, PrometheusURL: backend.URL, TempoURL: backend.URL})
	server := httptest.NewServer(service.Handler())
	t.Cleanup(server.Close)

	for _, endpoint := range []string{"/health", "/metrics", "/api/observability/overview", "/api/observability/alerts", "/api/observability/traces/4bf92f3577b34da6a3ce929d0e0e4736"} {
		response, err := http.Get(server.URL + endpoint)
		if err != nil {
			t.Fatal(err)
		}
		if response.StatusCode != http.StatusOK {
			response.Body.Close()
			t.Fatalf("%s status = %d", endpoint, response.StatusCode)
		}
		response.Body.Close()
	}
	alerts, err := service.Alerts(context.Background())
	if err != nil || len(alerts) != 1 || alerts[0].Severity != "critical" {
		t.Fatalf("alerts = %#v, err=%v", alerts, err)
	}
	overview := service.Overview(context.Background())
	if overview["status"] != "degraded" {
		t.Fatalf("overview without PostgreSQL must be degraded: %#v", overview)
	}
	metrics := overview["metrics"].([]Metric)
	if len(metrics) != 8 || metrics[1].Value != 1 {
		t.Fatalf("metrics = %#v", metrics)
	}
}

func TestHTTPRejectsUnboundedTimelineAndTraceInputs(t *testing.T) {
	service := New(nil, Config{})
	server := httptest.NewServer(service.Handler())
	t.Cleanup(server.Close)
	response, err := http.Get(server.URL + "/api/observability/timeline?" + url.Values{"entity_type": {"raw_sql"}, "entity_id": {"anything"}}.Encode())
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusUnprocessableEntity {
		t.Fatalf("timeline status = %d", response.StatusCode)
	}
	var payload map[string]map[string]any
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if payload["error"]["code"] != "TIMELINE_UNAVAILABLE" {
		t.Fatalf("payload = %#v", payload)
	}
}

func TestStandaloneUIAndOptionalTokenGate(t *testing.T) {
	service := New(nil, Config{AccessToken: "test-observability-token"})
	server := httptest.NewServer(service.Handler())
	t.Cleanup(server.Close)

	response, err := http.Get(server.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusUnauthorized {
		response.Body.Close()
		t.Fatalf("unauthenticated UI status = %d", response.StatusCode)
	}
	response.Body.Close()

	health, err := http.Get(server.URL + "/health")
	if err != nil {
		t.Fatal(err)
	}
	if health.StatusCode != http.StatusOK {
		health.Body.Close()
		t.Fatalf("health must remain public: status=%v", health.StatusCode)
	}
	health.Body.Close()

	apiRequest, _ := http.NewRequest(http.MethodGet, server.URL+"/api/observability/overview", nil)
	apiRequest.Header.Set("Authorization", "Bearer test-observability-token")
	apiResponse, err := http.DefaultClient.Do(apiRequest)
	if err != nil {
		t.Fatal(err)
	}
	if apiResponse.StatusCode != http.StatusOK {
		apiResponse.Body.Close()
		t.Fatalf("bearer API status=%v", apiResponse.StatusCode)
	}
	apiResponse.Body.Close()

	client := &http.Client{CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	login, err := client.Get(server.URL + "/?token=test-observability-token")
	if err != nil {
		t.Fatal(err)
	}
	if login.StatusCode != http.StatusSeeOther || login.Header.Get("Location") != "/" || len(login.Cookies()) != 1 {
		login.Body.Close()
		t.Fatalf("login response status=%d location=%q cookies=%d", login.StatusCode, login.Header.Get("Location"), len(login.Cookies()))
	}
	cookie := login.Cookies()[0]
	login.Body.Close()

	uiRequest, _ := http.NewRequest(http.MethodGet, server.URL+"/", nil)
	uiRequest.AddCookie(cookie)
	uiResponse, err := http.DefaultClient.Do(uiRequest)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(uiResponse.Body)
	uiResponse.Body.Close()
	if uiResponse.StatusCode != http.StatusOK || !strings.Contains(string(body), "日志与系统观测") {
		t.Fatalf("authenticated UI status=%d body=%q", uiResponse.StatusCode, string(body))
	}
	assetRequest, _ := http.NewRequest(http.MethodGet, server.URL+"/console.js", nil)
	assetRequest.AddCookie(cookie)
	assetResponse, err := http.DefaultClient.Do(assetRequest)
	if err != nil {
		t.Fatal(err)
	}
	assetBody, _ := io.ReadAll(assetResponse.Body)
	assetResponse.Body.Close()
	if assetResponse.StatusCode != http.StatusOK || !strings.Contains(string(assetBody), "loadOverview") {
		t.Fatalf("authenticated asset status=%d", assetResponse.StatusCode)
	}
}
