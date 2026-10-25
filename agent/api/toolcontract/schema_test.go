package toolcontract

import (
	"encoding/json"
	"testing"
)

func TestOpenAPICoversEveryExecutableToolAndApproval(t *testing.T) {
	spec := OpenAPI()
	raw, err := json.Marshal(spec)
	if err != nil || !json.Valid(raw) {
		t.Fatal(err)
	}
	paths := spec["paths"].(map[string]any)
	if len(paths) != 20 {
		t.Fatalf("paths=%d", len(paths))
	}
	for _, d := range Definitions() {
		if paths["/api/agent-tools/invoke/"+d.Name] == nil {
			t.Fatal("missing invoke", d.Name)
		}
		_, approval := paths["/api/agent-tools/approvals/"+d.Name]
		if approval == d.ReadOnly {
			t.Fatal("incorrect approval route", d.Name)
		}
	}
}
