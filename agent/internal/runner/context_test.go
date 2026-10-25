package runner

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestDatasetProjectionRetainsIDsAndFactsWithoutClassBreakdown(t *testing.T) {
	raw := json.RawMessage(`{"result":{"tool":"datasets.list","data":{"datasets":[{"id":"dataset-id","latest_version_id":"version-id","name":"鸟类","class_count":200,"classes":["very-large-breakdown"],"versions":[{"old":"history"}]}],"pagination":{"total":80}}}}`)
	out := compactContext(raw)
	for _, required := range []string{"dataset-id", "version-id", "class_count", "200", "pagination", "context_projection"} {
		if !bytes.Contains(out, []byte(required)) {
			t.Fatalf("lost %s: %s", required, out)
		}
	}
	if bytes.Contains(out, []byte("very-large-breakdown")) {
		t.Fatal("class data was not compacted")
	}
	if !bytes.Contains(raw, []byte("very-large-breakdown")) {
		t.Fatal("source evidence mutated")
	}
}
