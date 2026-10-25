package training

import (
	"context"
	"sync"
	"testing"
	"time"
)

func TestIdempotentCreateSurvivesConcurrentCallsAndServiceRestart(t *testing.T) {
	repo := NewMemoryRepository()
	service := NewService(repo, time.Now, time.Minute)
	command := CreateCommand{OperationKey: "actor:operation", DatasetID: "dataset", DatasetVersionID: "v", BackboneID: "backbone", Payload: map[string]any{"head_config": map[string]any{"epochs": 10}}}
	var wg sync.WaitGroup
	created := make(chan CreatedJob, 30)
	for i := 0; i < 30; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			value, err := service.Create(context.Background(), command)
			if err != nil {
				t.Error(err)
				return
			}
			created <- value
		}()
	}
	wg.Wait()
	close(created)
	var first CreatedJob
	for value := range created {
		if first.JobID == "" {
			first = value
		}
		if value != first {
			t.Fatalf("duplicate tasks: %+v %+v", first, value)
		}
	}
	if first.JobID == "" {
		t.Fatal("nothing created")
	}
	aggregate, _ := repo.Get(context.Background(), first.JobID)
	if len(aggregate.Outbox) != 1 {
		t.Fatal("duplicate outbox")
	}
	restarted := NewService(repo, time.Now, time.Minute)
	again, err := restarted.Create(context.Background(), command)
	if err != nil || again != first {
		t.Fatalf("restart changed identity: %+v %v", again, err)
	}
	command.Payload = map[string]any{"head_config": map[string]any{"epochs": 20}}
	if _, err := restarted.Create(context.Background(), command); ErrorCode(err) != CodeIdempotencyConflict {
		t.Fatalf("conflicting reuse: %v", err)
	}
}
