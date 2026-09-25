package observabilityconsole

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Config struct {
	LokiURL       string
	PrometheusURL string
	TempoURL      string
	AccessToken   string
}

type Service struct {
	pool            *pgxpool.Pool
	client          *http.Client
	config          Config
	accessTokenHash [32]byte
	authEnabled     bool
}

type Component struct {
	Name   string `json:"name"`
	Status string `json:"status"`
	Detail string `json:"detail,omitempty"`
}

type Metric struct {
	Key     string  `json:"key"`
	Label   string  `json:"label"`
	Value   float64 `json:"value"`
	Unit    string  `json:"unit,omitempty"`
	Tone    string  `json:"tone"`
	Caption string  `json:"caption,omitempty"`
}

type LogEntry struct {
	Timestamp string         `json:"timestamp"`
	Category  string         `json:"category"`
	Service   string         `json:"service"`
	Level     string         `json:"level"`
	Event     string         `json:"event,omitempty"`
	Message   string         `json:"message"`
	Outcome   string         `json:"outcome,omitempty"`
	RequestID string         `json:"request_id,omitempty"`
	TraceID   string         `json:"trace_id,omitempty"`
	Fields    map[string]any `json:"fields,omitempty"`
	UnixNano  int64          `json:"-"`
}

type LogFilter struct {
	Category string
	Service  string
	Level    string
	Outcome  string
	Search   string
	Range    time.Duration
	End      time.Time
	Limit    int
}

type LogPage struct {
	Items   []LogEntry `json:"items"`
	HasMore bool       `json:"has_more"`
	NextEnd string     `json:"next_end,omitempty"`
	Query   string     `json:"query_summary"`
}

type Alert struct {
	Name     string            `json:"name"`
	State    string            `json:"state"`
	Severity string            `json:"severity"`
	Summary  string            `json:"summary"`
	ActiveAt string            `json:"active_at,omitempty"`
	Labels   map[string]string `json:"labels,omitempty"`
	Value    string            `json:"value,omitempty"`
}

type TimelineEvent struct {
	Timestamp string         `json:"timestamp"`
	Signal    string         `json:"signal"`
	Category  string         `json:"category"`
	Event     string         `json:"event"`
	Message   string         `json:"message"`
	Actor     string         `json:"actor,omitempty"`
	From      string         `json:"from_status,omitempty"`
	To        string         `json:"to_status,omitempty"`
	Service   string         `json:"service,omitempty"`
	Level     string         `json:"level,omitempty"`
	TraceID   string         `json:"trace_id,omitempty"`
	Payload   map[string]any `json:"payload,omitempty"`
	UnixNano  int64          `json:"-"`
}

var categoryServices = map[string]string{
	"control":    "go-control-plane|outbox-relay",
	"training":   "python-training-worker",
	"inference":  "python-inference-runtime|python-artifact-runtime",
	"deployment": "deployment-relay|deployment-worker|tensorrt-builder|ascend-builder",
	"annotation": "annotation-worker|annotation-agent",
	"llm":        "go-llm-gateway",
	"hardware":   "hardware-collector|tensorrt-inference|ascend-inference",
}

var safeLabel = regexp.MustCompile(`^[a-zA-Z0-9_.:-]{1,80}$`)
var traceIDPattern = regexp.MustCompile(`^[a-fA-F0-9]{16,32}$`)

func New(pool *pgxpool.Pool, config Config) *Service {
	config.LokiURL = normalizedURL(config.LokiURL, "http://loki:3100")
	config.PrometheusURL = normalizedURL(config.PrometheusURL, "http://prometheus:9090")
	config.TempoURL = normalizedURL(config.TempoURL, "http://tempo:3200")
	service := &Service{pool: pool, config: config, client: &http.Client{Timeout: 5 * time.Second}}
	if config.AccessToken != "" {
		service.accessTokenHash = sha256.Sum256([]byte(config.AccessToken))
		service.authEnabled = true
		service.config.AccessToken = ""
	}
	return service
}

func normalizedURL(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		value = fallback
	}
	return strings.TrimRight(value, "/")
}

func (service *Service) Overview(ctx context.Context) map[string]any {
	components := service.components(ctx)
	status := "healthy"
	for _, component := range components {
		if component.Status != "online" {
			status = "degraded"
			break
		}
	}
	metrics := service.metrics(ctx)
	result := map[string]any{
		"generated_at": time.Now().UTC(),
		"status":       status,
		"components":   components,
		"metrics":      metrics,
	}
	if service.pool != nil {
		result["queues"] = service.queueSummary(ctx)
	}
	return result
}

func (service *Service) components(ctx context.Context) []Component {
	type probe struct{ name, target string }
	probes := []probe{{"Loki", service.config.LokiURL + "/ready"}, {"Prometheus", service.config.PrometheusURL + "/-/ready"}, {"Tempo", service.config.TempoURL + "/ready"}}
	result := make([]Component, len(probes)+1)
	var wait sync.WaitGroup
	for index, current := range probes {
		wait.Add(1)
		go func(index int, current probe) {
			defer wait.Done()
			request, _ := http.NewRequestWithContext(ctx, http.MethodGet, current.target, nil)
			response, err := service.client.Do(request)
			if err != nil {
				result[index] = Component{Name: current.name, Status: "unavailable", Detail: boundedError(err)}
				return
			}
			defer response.Body.Close()
			if response.StatusCode < 200 || response.StatusCode >= 300 {
				result[index] = Component{Name: current.name, Status: "unavailable", Detail: response.Status}
				return
			}
			result[index] = Component{Name: current.name, Status: "online"}
		}(index, current)
	}
	wait.Add(1)
	go func() {
		defer wait.Done()
		component := Component{Name: "PostgreSQL", Status: "unavailable", Detail: "未配置"}
		if service.pool != nil {
			pingCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
			defer cancel()
			if err := service.pool.Ping(pingCtx); err == nil {
				component = Component{Name: "PostgreSQL", Status: "online"}
			} else {
				component.Detail = boundedError(err)
			}
		}
		result[len(result)-1] = component
	}()
	wait.Wait()
	return result
}

func (service *Service) metrics(ctx context.Context) []Metric {
	specs := []struct {
		key, label, query, unit, caption string
		warn, danger                     float64
	}{
		{"request_rate", "API 请求速率", `sum(rate(finevision_http_requests_total[5m]))`, "req/s", "最近 5 分钟", math.Inf(1), math.Inf(1)},
		{"error_rate", "API 5xx 比例", `100 * sum(rate(finevision_http_requests_total{status=~"5.."}[5m])) / clamp_min(sum(rate(finevision_http_requests_total[5m])), 0.001)`, "%", "持续超过 2% 需关注", 2, 5},
		{"outbox", "Outbox 积压", `sum(finevision_outbox_pending)`, "条", "等待发布的领域事件", 1, 20},
		{"targets_down", "异常目标", `sum(up{job=~"finevision-.*|rabbitmq|postgres",required!="false"} == 0)`, "个", "必需采集目标", 1, 2},
		{"worker_failures", "Worker 失败", `sum(increase(finevision_runtime_work_total{outcome="failed"}[1h]))`, "次", "最近 1 小时", 1, 5},
		{"llm_errors", "LLM 异常", `sum(increase(finevision_llm_requests_total{outcome=~"rate_limited|server_error|failed|unknown"}[1h]))`, "次", "最近 1 小时", 1, 5},
		{"integrity", "完整性失败", `sum(increase(finevision_artifact_integrity_failures_total[24h]))`, "次", "最近 24 小时", 1, 1},
		{"dead_letters", "死信消息", `sum(rabbitmq_queue_messages_ready{queue=~".*(dlq|dead).*"})`, "条", "RabbitMQ DLQ", 1, 1},
	}
	metrics := make([]Metric, len(specs))
	var wait sync.WaitGroup
	for index, spec := range specs {
		wait.Add(1)
		go func(index int, spec struct {
			key, label, query, unit, caption string
			warn, danger                     float64
		}) {
			defer wait.Done()
			value, err := service.prometheusValue(ctx, spec.query)
			tone := "neutral"
			if err != nil {
				value, tone = 0, "unknown"
			} else if value >= spec.danger {
				tone = "danger"
			} else if value >= spec.warn {
				tone = "warning"
			} else {
				tone = "positive"
			}
			metrics[index] = Metric{Key: spec.key, Label: spec.label, Value: value, Unit: spec.unit, Tone: tone, Caption: spec.caption}
		}(index, spec)
	}
	wait.Wait()
	return metrics
}

func (service *Service) prometheusValue(ctx context.Context, query string) (float64, error) {
	endpoint := service.config.PrometheusURL + "/api/v1/query?" + url.Values{"query": {query}}.Encode()
	var payload struct {
		Status string `json:"status"`
		Data   struct {
			Result []struct {
				Value []json.RawMessage `json:"value"`
			} `json:"result"`
		} `json:"data"`
	}
	if err := service.getJSON(ctx, endpoint, &payload); err != nil {
		return 0, err
	}
	if payload.Status != "success" || len(payload.Data.Result) == 0 || len(payload.Data.Result[0].Value) < 2 {
		return 0, nil
	}
	var raw string
	if err := json.Unmarshal(payload.Data.Result[0].Value[1], &raw); err != nil {
		return 0, err
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) {
		return 0, err
	}
	return value, nil
}

func (service *Service) Logs(ctx context.Context, filter LogFilter) (LogPage, error) {
	filter = normalizeFilter(filter)
	query, summary := buildLogQuery(filter)
	values := url.Values{
		"query":     {query},
		"start":     {strconv.FormatInt(filter.End.Add(-filter.Range).UnixNano(), 10)},
		"end":       {strconv.FormatInt(filter.End.UnixNano(), 10)},
		"limit":     {strconv.Itoa(filter.Limit + 1)},
		"direction": {"backward"},
	}
	var payload struct {
		Status string `json:"status"`
		Data   struct {
			Result []struct {
				Stream map[string]string `json:"stream"`
				Values [][]string        `json:"values"`
			} `json:"result"`
		} `json:"data"`
	}
	if err := service.getJSON(ctx, service.config.LokiURL+"/loki/api/v1/query_range?"+values.Encode(), &payload); err != nil {
		return LogPage{}, err
	}
	items := make([]LogEntry, 0, filter.Limit+1)
	for _, stream := range payload.Data.Result {
		for _, value := range stream.Values {
			if len(value) < 2 {
				continue
			}
			nanoseconds, _ := strconv.ParseInt(value[0], 10, 64)
			items = append(items, parseLogEntry(nanoseconds, value[1], stream.Stream))
		}
	}
	sort.Slice(items, func(left, right int) bool { return items[left].UnixNano > items[right].UnixNano })
	hasMore := len(items) > filter.Limit
	if hasMore {
		items = items[:filter.Limit]
	}
	page := LogPage{Items: items, HasMore: hasMore, Query: summary}
	if len(items) > 0 {
		page.NextEnd = strconv.FormatInt(items[len(items)-1].UnixNano-1, 10)
	}
	return page, nil
}

func normalizeFilter(filter LogFilter) LogFilter {
	if filter.Range <= 0 || filter.Range > 7*24*time.Hour {
		filter.Range = time.Hour
	}
	if filter.End.IsZero() {
		filter.End = time.Now()
	}
	if filter.Limit <= 0 {
		filter.Limit = 50
	}
	if filter.Limit > 200 {
		filter.Limit = 200
	}
	return filter
}

func buildLogQuery(filter LogFilter) (string, string) {
	selectors := []string{`platform="finevision"`}
	query := "{" + strings.Join(selectors, ",") + "} | json"
	if services, ok := categoryServices[filter.Category]; ok {
		query += ` | service=~"` + services + `" or compose_service=~"` + services + `"`
	} else if safeLabel.MatchString(filter.Service) {
		query += ` | service="` + filter.Service + `" or compose_service="` + filter.Service + `"`
	}
	if level := strings.ToUpper(filter.Level); level == "DEBUG" || level == "INFO" || level == "WARN" || level == "ERROR" {
		query += " |~ " + strconv.Quote(levelLinePattern(level))
	}
	if outcome := strings.ToLower(filter.Outcome); outcome == "success" || outcome == "failed" || outcome == "retry" || outcome == "unknown" || outcome == "rejected" {
		query += ` | outcome="` + outcome + `"`
	}
	search := strings.TrimSpace(filter.Search)
	if search != "" {
		if len(search) > 160 {
			search = search[:160]
		}
		query += " |~ " + strconv.Quote(regexp.QuoteMeta(search))
	}
	summary := "全部服务"
	if filter.Category != "" && filter.Category != "all" {
		summary = filter.Category
	} else if filter.Service != "" {
		summary = filter.Service
	}
	return query, summary
}

func levelLinePattern(level string) string {
	token := level
	if level == "WARN" {
		token = `WARN(ING)?`
	}
	return `(?i)("level"\s*:\s*"` + token + `"|^` + token + `[: ]|^[0-9]{4}[-/].{0,32} ` + token + `[: ]|:` + token + `:)`
}

func parseLogEntry(nanoseconds int64, line string, stream map[string]string) LogEntry {
	fields := map[string]any{}
	_ = json.Unmarshal([]byte(line), &fields)
	service := stringField(fields, "service", stream["service"], stream["compose_service"])
	message := stringField(fields, "message")
	if message == "" {
		message = line
	}
	entry := LogEntry{
		Timestamp: time.Unix(0, nanoseconds).UTC().Format(time.RFC3339Nano),
		UnixNano:  nanoseconds,
		Category:  categoryForService(service),
		Service:   service,
		Level:     strings.ToUpper(stringField(fields, "level", stream["level"])),
		Event:     stringField(fields, "event"), Message: message,
		Outcome: stringField(fields, "outcome"), RequestID: stringField(fields, "request_id"), TraceID: stringField(fields, "trace_id"),
		Fields: fields,
	}
	entry.Level = inferredLevel(entry.Level, entry.Message)
	for _, key := range []string{"timestamp", "level", "service", "event", "message", "outcome", "request_id", "trace_id", "span_id"} {
		delete(entry.Fields, key)
	}
	return entry
}

func inferredLevel(level, message string) string {
	upper := strings.ToUpper(strings.TrimSpace(message))
	if strings.HasPrefix(upper, "WARNING") || strings.Contains(upper, " WARNING ") || strings.Contains(upper, ":WARNING:") {
		return "WARN"
	}
	for _, candidate := range []string{"ERROR", "WARN", "DEBUG"} {
		if strings.HasPrefix(upper, candidate) || strings.Contains(upper, ":"+candidate+":") || strings.Contains(upper, " "+candidate+" ") {
			return candidate
		}
	}
	if level == "" {
		return "INFO"
	}
	return level
}

func categoryForService(service string) string {
	for category, expression := range categoryServices {
		for _, candidate := range strings.Split(expression, "|") {
			if service == candidate {
				return category
			}
		}
	}
	return "system"
}

func stringField(fields map[string]any, key string, fallbacks ...string) string {
	if value, ok := fields[key].(string); ok && value != "" {
		return value
	}
	for _, fallback := range fallbacks {
		if fallback != "" {
			return fallback
		}
	}
	return ""
}

func (service *Service) Alerts(ctx context.Context) ([]Alert, error) {
	var payload struct {
		Status string `json:"status"`
		Data   struct {
			Alerts []struct {
				Labels      map[string]string `json:"labels"`
				Annotations map[string]string `json:"annotations"`
				State       string            `json:"state"`
				ActiveAt    string            `json:"activeAt"`
				Value       string            `json:"value"`
			} `json:"alerts"`
		} `json:"data"`
	}
	if err := service.getJSON(ctx, service.config.PrometheusURL+"/api/v1/alerts", &payload); err != nil {
		return nil, err
	}
	alerts := make([]Alert, 0, len(payload.Data.Alerts))
	for _, item := range payload.Data.Alerts {
		alerts = append(alerts, Alert{Name: item.Labels["alertname"], State: item.State, Severity: valueOr(item.Labels["severity"], "warning"), Summary: valueOr(item.Annotations["summary"], item.Labels["alertname"]), ActiveAt: item.ActiveAt, Labels: item.Labels, Value: item.Value})
	}
	sort.Slice(alerts, func(left, right int) bool { return alerts[left].ActiveAt > alerts[right].ActiveAt })
	return alerts, nil
}

func (service *Service) Timeline(ctx context.Context, entityType, entityID string, period time.Duration) ([]TimelineEvent, error) {
	if _, err := uuid.Parse(entityID); err != nil {
		return nil, fmt.Errorf("invalid entity id")
	}
	if _, ok := auditQueries[entityType]; !ok {
		return nil, fmt.Errorf("unsupported entity type")
	}
	events, auditErr := service.auditTimeline(ctx, entityType, entityID)
	logs, logErr := service.Logs(ctx, LogFilter{Search: entityID, Range: period, Limit: 100})
	for _, item := range logs.Items {
		events = append(events, TimelineEvent{Timestamp: item.Timestamp, UnixNano: item.UnixNano, Signal: "log", Category: item.Category, Event: valueOr(item.Event, "runtime_log"), Message: item.Message, Service: item.Service, Level: item.Level, TraceID: item.TraceID, Payload: item.Fields})
	}
	sort.Slice(events, func(left, right int) bool { return events[left].UnixNano < events[right].UnixNano })
	if auditErr != nil && logErr != nil {
		return nil, errors.Join(auditErr, logErr)
	}
	if auditErr != nil {
		return events, auditErr
	}
	if logErr != nil {
		return events, logErr
	}
	return events, nil
}

var auditQueries = map[string]string{
	"training_job":    `SELECT created_at,event_type,'system',NULL,NULL,COALESCE(message,''),payload FROM job_events WHERE job_id=$1 ORDER BY created_at`,
	"annotation_task": `SELECT created_at,event_type,actor,from_status,to_status,'',payload FROM annotation_task_events WHERE task_id=$1 ORDER BY created_at`,
	"deployment":      `SELECT created_at,event_type,actor,from_status,to_status,'',payload FROM model_deployment_events WHERE deployment_id=$1 ORDER BY created_at`,
}

func (service *Service) auditTimeline(ctx context.Context, entityType, entityID string) ([]TimelineEvent, error) {
	if service.pool == nil {
		return nil, fmt.Errorf("audit database unavailable")
	}
	rows, err := service.pool.Query(ctx, auditQueries[entityType], entityID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	events := []TimelineEvent{}
	for rows.Next() {
		var occurred time.Time
		var event, actor, message string
		var from, to *string
		var raw []byte
		if err := rows.Scan(&occurred, &event, &actor, &from, &to, &message, &raw); err != nil {
			return nil, err
		}
		payload := map[string]any{}
		_ = json.Unmarshal(raw, &payload)
		item := TimelineEvent{Timestamp: occurred.UTC().Format(time.RFC3339Nano), UnixNano: occurred.UnixNano(), Signal: "audit", Category: entityType, Event: event, Message: valueOr(message, humanEvent(event)), Actor: actor, Payload: payload}
		if from != nil {
			item.From = *from
		}
		if to != nil {
			item.To = *to
		}
		events = append(events, item)
	}
	return events, rows.Err()
}

func humanEvent(event string) string {
	return strings.ReplaceAll(strings.TrimSpace(event), "_", " ")
}

func (service *Service) Trace(ctx context.Context, traceID string) (json.RawMessage, error) {
	if !traceIDPattern.MatchString(traceID) {
		return nil, fmt.Errorf("invalid trace id")
	}
	request, _ := http.NewRequestWithContext(ctx, http.MethodGet, service.config.TempoURL+"/api/v2/traces/"+traceID, nil)
	response, err := service.client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil {
		return nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("tempo returned %s", response.Status)
	}
	if !json.Valid(body) {
		return nil, fmt.Errorf("tempo returned invalid json")
	}
	return body, nil
}

func (service *Service) queueSummary(ctx context.Context) map[string]any {
	result := map[string]any{}
	for key, query := range map[string]string{
		"training":   `SELECT count(*) FILTER(WHERE status='queued'),count(*) FILTER(WHERE status='running'),count(*) FILTER(WHERE status='failed') FROM jobs`,
		"annotation": `SELECT count(*) FILTER(WHERE status IN ('pending','queued')),count(*) FILTER(WHERE status='running'),count(*) FILTER(WHERE status IN ('failed','unknown')) FROM annotation_tasks`,
		"deployment": `SELECT count(*) FILTER(WHERE status='queued'),count(*) FILTER(WHERE status='building'),count(*) FILTER(WHERE status='failed') FROM model_deployments`,
	} {
		var queued, running, failed int64
		if err := service.pool.QueryRow(ctx, query).Scan(&queued, &running, &failed); err == nil {
			result[key] = map[string]int64{"queued": queued, "running": running, "failed": failed}
		}
	}
	var pending int64
	var oldest float64
	if err := service.pool.QueryRow(ctx, `SELECT count(*),COALESCE(EXTRACT(EPOCH FROM now()-min(created_at)),0) FROM outbox_events WHERE published_at IS NULL`).Scan(&pending, &oldest); err == nil {
		result["outbox"] = map[string]any{"pending": pending, "oldest_age_seconds": oldest}
	}
	return result
}

func (service *Service) getJSON(ctx context.Context, endpoint string, target any) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	response, err := service.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("backend returned %s", response.Status)
	}
	return json.NewDecoder(io.LimitReader(response.Body, 8<<20)).Decode(target)
}

func boundedError(err error) string {
	if err == nil {
		return ""
	}
	value := err.Error()
	if len(value) > 160 {
		value = value[:160]
	}
	return value
}

func valueOr(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}
