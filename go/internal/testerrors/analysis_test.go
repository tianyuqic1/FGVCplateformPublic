package testerrors

import (
	"encoding/json"
	"math"
	"os"
	"strings"
	"testing"
)

const run = "11111111-1111-4111-8111-111111111111"
const version = "22222222-2222-4222-8222-222222222222"

func fixture(t *testing.T) []byte {
	t.Helper()
	b, e := os.ReadFile("testdata/python-report.json")
	if e != nil {
		t.Fatal(e)
	}
	return b
}
func TestPythonEvidencePreservesTestScopeAndBounds(t *testing.T) {
	out, e := Analyze(fixture(t), run, "", version, "", nil, nil, 1, 1)
	if e != nil {
		t.Fatal(e)
	}
	if out.Availability != "available" || out.Evidence.Samples != 3 || out.Evidence.Errors != 2 || len(out.Evidence.Pairs) != 1 || len(out.Evidence.Examples) != 1 || out.Evidence.Examples[0].ID != "wrong" {
		t.Fatalf("bad analysis %+v", out)
	}
	if out.Evidence.MacroF1 == nil || math.Abs(*out.Evidence.MacroF1-0.25) > 1e-8 {
		t.Fatalf("incorrect test-only macro-F1 %+v", out.Evidence.MacroF1)
	}
	if len(out.Findings) < 3 || out.Findings[1].Kind != "hypothesis" {
		t.Fatalf("missing uncertainty labels %+v", out.Findings)
	}
	for _, x := range out.Evidence.Examples {
		if x.ID == "train" || x.ID == "val" {
			t.Fatal("non-test sample leaked")
		}
	}
}
func TestUnknownLegacyScopeNeverBecomesTestEvidence(t *testing.T) {
	raw := []byte(`{"run_id":"` + run + `","dataset_version_id":"` + version + `","evaluation":{"confusion_matrix":[[0,5],[0,2]],"per_class":{"a":{"support":5},"b":{"support":2}}}}`)
	out, e := Analyze(raw, run, "", version, "", []string{"a", "b"}, nil, 10, 10)
	if e != nil || out.Availability != "unavailable" || out.Evidence.Samples != 0 || len(out.Limitations) == 0 {
		t.Fatalf("mixed-split metrics claimed as test %+v %v", out, e)
	}
}
func TestHistoricalTestMatrixUsesVerifiedModelClassOrder(t *testing.T) {
	raw := []byte(`{"run_id":"` + run + `","dataset_version_id":"` + version + `","run_config":{"evaluation_split":"test"},"evaluation":{"confusion_matrix":[[2,1],[0,2]],"per_class":{"ant":{"support":2},"zebra":{"support":3}}}}`)
	out, e := Analyze(raw, run, "", version, "", []string{"zebra", "ant"}, map[string]int{"zebra": 2}, 10, 10)
	if e != nil || out.Availability != "aggregate_only" || out.Evidence.Errors != 1 || out.Evidence.Pairs[0].True != "zebra" || out.Evidence.HighErrors != nil || len(out.Evidence.Examples) != 0 {
		t.Fatalf("bad historical report %+v %v", out, e)
	}
	out, e = Analyze(raw, run, "", version, "", []string{"ant", "zebra"}, nil, 10, 10)
	if e != nil || len(out.Evidence.Pairs) != 0 {
		t.Fatalf("guessed wrong class ordering %+v %v", out, e)
	}
}
func TestMalformedCountsAndScopeAreRejected(t *testing.T) {
	base := string(fixture(t))
	for _, bad := range []string{strings.Replace(base, `"error_count": 2`, `"error_count": 9`, 1), strings.Replace(base, run, "other-run", 1), strings.Replace(base, version, "other-version", 1)} {
		if _, e := Analyze([]byte(bad), run, "", version, "", nil, nil, 10, 10); e == nil {
			t.Fatal("invalid report accepted")
		}
	}
}
func TestZeroErrorAndEmptyTestRemainDistinct(t *testing.T) {
	var d Document
	if e := json.Unmarshal(fixture(t), &d); e != nil {
		t.Fatal(e)
	}
	e := d.Evaluation.Analysis
	e.Errors = 0
	e.Accuracy = new(float64)
	*e.Accuracy = 1
	e.Pairs = nil
	e.PairTotal = 0
	e.Examples = nil
	e.Retained = 0
	*e.HighErrors = 0
	*e.LowErrors = 0
	for i := range e.Classes {
		e.Classes[i].ErrorCount = 0
	}
	raw, _ := json.Marshal(d)
	out, err := Analyze(raw, run, "", version, "", nil, nil, 10, 10)
	if err != nil || out.Availability != "available" || len(out.Findings) != 1 || out.Findings[0].Kind != "observed" {
		t.Fatalf("zero errors %+v %v", out, err)
	}
	e.Samples = 0
	e.Accuracy = nil
	for i := range e.Classes {
		e.Classes[i].TestCount = 0
	}
	raw, _ = json.Marshal(d)
	out, err = Analyze(raw, run, "", version, "", nil, nil, 10, 10)
	if err != nil || out.Availability != "unavailable" {
		t.Fatalf("empty tests treated as success %+v %v", out, err)
	}
}
