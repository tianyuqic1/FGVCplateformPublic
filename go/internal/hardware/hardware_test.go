package hardware

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
)

func ptr(v float64) *float64 { return &v }
func fixture(now time.Time) Snapshot {
	return Snapshot{NodeID: "test-node", Name: "Test node", Scope: "host", SampledAt: now, ReceivedAt: now, CPU: CPU{Percent: ptr(30), Cores: 8}, Memory: Memory{ptr(95), ptr(100)}, GPUStatus: "none", GPUs: []GPU{}, Disks: []Disk{}, Errors: []string{}}
}

type memoryStore struct {
	samples     []Snapshot
	historyStep int
}

func (s *memoryStore) Ingest(_ context.Context, v Snapshot) error {
	if len(s.samples) > 0 && !v.SampledAt.After(s.samples[len(s.samples)-1].SampledAt) {
		return ErrOldSample
	}
	s.samples = append(s.samples, v)
	return nil
}
func (s *memoryStore) Nodes(context.Context) ([]Snapshot, error) { return s.samples, nil }
func (s *memoryStore) Latest(_ context.Context, id string) (Snapshot, error) {
	for _, v := range s.samples {
		if v.NodeID == id {
			return v, nil
		}
	}
	return Snapshot{}, ErrNotFound
}
func (s *memoryStore) History(_ context.Context, _ string, _ time.Time, step int) ([]Snapshot, error) {
	if step > 1 {
		s.historyStep = step
	}
	return s.samples, nil
}
func (s *memoryStore) Tasks(context.Context, string) ([]Task, error) { return []Task{}, nil }
func TestValidationAndOffline(t *testing.T) {
	now := time.Now().UTC()
	s := fixture(now)
	if err := s.Validate(now); err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		age    time.Duration
		status string
	}{{5 * time.Second, "online"}, {16 * time.Second, "stale"}, {61 * time.Second, "offline"}} {
		if got := s.Status(now.Add(test.age)); got != test.status {
			t.Fatal(got)
		}
	}
	s.CPU.Percent = ptr(101)
	if s.Validate(now) == nil {
		t.Fatal("accepted CPU >100")
	}
	s.CPU.Percent = nil
	s.Memory.Used = nil
	if s.Validate(now) != nil {
		t.Fatal("missing metric must be allowed")
	}
	s.SampledAt = now.Add(-31 * time.Second)
	if s.Validate(now) == nil {
		t.Fatal("accepted stale sample")
	}
	s.SampledAt = now
	s.GPUStatus = "none"
	s.GPUs = []GPU{{ID: "GPU-a", Name: "GPU", Memory: Memory{ptr(1), ptr(2)}}}
	if s.Validate(now) == nil {
		t.Fatal("accepted contradictory GPU status")
	}
}
func TestAlertsNeedContinuousPressure(t *testing.T) {
	now := time.Now().UTC()
	samples := []Snapshot{}
	for i := 0; i <= 12; i++ {
		samples = append(samples, fixture(now.Add(time.Duration(i-12)*5*time.Second)))
	}
	if len(Alerts(samples, now)) != 1 {
		t.Fatal("missing sustained memory alert")
	}
	if len(Alerts(samples[1:], now)) != 0 {
		t.Fatal("alert before 60 seconds")
	}
	samples[5].Memory.Used = nil
	if len(Alerts(samples, now)) != 0 {
		t.Fatal("unknown metric treated as pressure")
	}
	samples[5].Memory.Used = ptr(95)
	withGap := append(append([]Snapshot{}, samples[:3]...), samples[7:]...)
	if len(Alerts(withGap, now)) != 0 {
		t.Fatal("gap treated as continuous")
	}
	if len(Alerts(samples, now.Add(time.Minute))) != 0 {
		t.Fatal("stale readings generated live alerts")
	}
}
func TestHTTPCollectorAndHistory(t *testing.T) {
	now := time.Now().UTC()
	store := &memoryStore{}
	router := chi.NewRouter()
	Handler{Store: store, Token: "private-token", Now: func() time.Time { return now }}.Register(router)
	sample := fixture(now)
	sample.ReceivedAt = now.Add(2 * time.Hour)
	raw, _ := json.Marshal(sample)
	request := func(method, path, token string, body []byte) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, bytes.NewReader(body))
		r.Header.Set("Authorization", token)
		w := httptest.NewRecorder()
		router.ServeHTTP(w, r)
		return w
	}
	if w := request("POST", "/api/internal/hardware/samples", "", raw); w.Code != 401 {
		t.Fatal(w.Code)
	}
	if w := request("POST", "/api/internal/hardware/samples", "Bearer private-token", append(append([]byte{}, raw...), raw...)); w.Code != 400 {
		t.Fatal("accepted trailing JSON", w.Code)
	}
	if w := request("POST", "/api/internal/hardware/samples", "Bearer private-token", raw); w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	if !store.samples[0].ReceivedAt.Equal(now) {
		t.Fatal("trusted collector receive time")
	}
	if w := request("POST", "/api/internal/hardware/samples", "Bearer private-token", raw); w.Code != 409 {
		t.Fatal("accepted duplicate", w.Code)
	}
	if w := request("GET", "/api/hardware/nodes/test-node?window=24h", "", nil); w.Code != http.StatusOK || store.historyStep != 300 {
		t.Fatal(w.Code, store.historyStep, w.Body.String())
	}
	for _, path := range []string{"/api/hardware/nodes/test-node?window=forever", "/api/hardware/nodes/invalid!"} {
		if w := request("GET", path, "", nil); w.Code != 400 {
			t.Fatal(path, w.Code)
		}
	}
	if w := request("GET", "/api/hardware/nodes/missing", "", nil); w.Code != 404 {
		t.Fatal(w.Code)
	}
}
