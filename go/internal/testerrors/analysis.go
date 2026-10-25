// Package testerrors summarizes persisted evidence, never reruns inference or tunes on test data.
package testerrors

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
)

type Class struct {
	Label      string   `json:"label"`
	TestCount  int      `json:"test_count"`
	ErrorCount int      `json:"error_count"`
	ErrorRate  *float64 `json:"error_rate"`
	TrainCount *int     `json:"train_count"`
}
type Pair struct {
	True      string  `json:"true_label"`
	Predicted string  `json:"predicted_label"`
	Count     int     `json:"count"`
	Rate      float64 `json:"rate_within_true_class"`
}
type Example struct {
	ID              string  `json:"sample_id"`
	True            string  `json:"true_label"`
	Predicted       string  `json:"predicted_label"`
	Confidence      float64 `json:"predicted_probability"`
	TrueProbability float64 `json:"true_probability"`
	Margin          float64 `json:"margin"`
	Top             []struct {
		Label       string  `json:"label"`
		Probability float64 `json:"probability"`
	} `json:"top_predictions"`
}
type Evidence struct {
	Version         int       `json:"schema_version"`
	Split           string    `json:"evaluation_split"`
	Selection       string    `json:"selection_basis"`
	Samples         int       `json:"sample_count"`
	Errors          int       `json:"error_count"`
	Accuracy        *float64  `json:"accuracy"`
	MacroF1         *float64  `json:"macro_f1,omitempty"`
	ConfidenceBasis string    `json:"confidence_basis"`
	HighThreshold   float64   `json:"high_confidence_threshold"`
	HighErrors      *int      `json:"high_confidence_error_count,omitempty"`
	LowThreshold    float64   `json:"low_margin_threshold"`
	LowErrors       *int      `json:"low_margin_error_count,omitempty"`
	Classes         []Class   `json:"class_statistics"`
	Pairs           []Pair    `json:"confusion_pairs"`
	PairTotal       int       `json:"confusion_pairs_total"`
	Examples        []Example `json:"examples"`
	Retained        int       `json:"examples_retained"`
	Policy          string    `json:"examples_policy"`
}
type Finding struct {
	Kind     string `json:"kind"`
	Claim    string `json:"claim"`
	Evidence any    `json:"evidence"`
	NextStep string `json:"next_step"`
}
type Report struct {
	RunID               string    `json:"run_id"`
	DatasetVersionID    string    `json:"dataset_version_id"`
	Availability        string    `json:"availability"`
	Evidence            Evidence  `json:"test_evidence"`
	ClassTotal          int       `json:"class_count"`
	ClassStatsTruncated bool      `json:"class_statistics_truncated"`
	Findings            []Finding `json:"findings"`
	Limitations         []string  `json:"limitations"`
}
type Document struct {
	RunID            string `json:"run_id"`
	DatasetVersionID string `json:"dataset_version_id"`
	Evaluation       struct {
		Accuracy float64 `json:"accuracy"`
		PerClass map[string]struct {
			Support float64 `json:"support"`
			Recall  float64 `json:"recall"`
		} `json:"per_class"`
		Matrix [][]int `json:"confusion_matrix"`
		Config struct {
			Split string `json:"evaluation_split"`
		} `json:"run_config"`
		Analysis *Evidence `json:"error_analysis"`
	} `json:"evaluation"`
	Config struct {
		Split string `json:"evaluation_split"`
	} `json:"run_config"`
}

func Analyze(raw []byte, runID, runKey, versionID, versionKey string, modelClasses []string, trainCounts map[string]int, maxPairs, maxExamples int) (Report, error) {
	out := Report{RunID: runID, DatasetVersionID: versionID, Availability: "unavailable", Findings: []Finding{}, Limitations: []string{}, Evidence: Evidence{Split: "test", Classes: []Class{}, Pairs: []Pair{}, Examples: []Example{}}}
	var doc Document
	if json.Unmarshal(raw, &doc) != nil {
		return out, fmt.Errorf("invalid training report")
	}
	if (doc.RunID != runID && doc.RunID != runKey) || (doc.DatasetVersionID != versionID && doc.DatasetVersionID != versionKey) {
		return out, fmt.Errorf("training report scope mismatch")
	}
	if maxPairs < 1 || maxPairs > 20 || maxExamples < 1 || maxExamples > 20 {
		return out, fmt.Errorf("invalid analysis bounds")
	}
	if doc.Evaluation.Analysis != nil {
		e := doc.Evaluation.Analysis
		if err := validate(*e); err != nil {
			return out, err
		}
		out.Evidence = *e
		out.Availability = "available"
	} else {
		if doc.Evaluation.Config.Split != "test" && doc.Config.Split != "test" {
			out.Limitations = append(out.Limitations, "历史报告未证明指标来自独立测试集，可能混合验证集；不能当作最终测试集错分分析。新训练会保存独立测试证据，读取本工具不会自动重训或重跑推理。")
			return out, nil
		}
		e := &out.Evidence
		e.Version = 1
		e.Selection = "persisted_test_confusion_matrix"
		e.ConfidenceBasis = "not_recorded"
		e.Policy = "not_recorded"
		matrix := doc.Evaluation.Matrix
		if len(matrix) == 0 || len(matrix) > 2000 {
			return out, fmt.Errorf("missing or oversized test confusion matrix")
		}
		for i, row := range matrix {
			if len(row) != len(matrix) {
				return out, fmt.Errorf("invalid confusion matrix")
			}
			for j, n := range row {
				if n < 0 || n > 1000000000 {
					return out, fmt.Errorf("invalid confusion count")
				}
				e.Samples += n
				if i != j {
					e.Errors += n
				}
			}
		}
		if e.Samples > 0 {
			accuracy := float64(e.Samples-e.Errors) / float64(e.Samples)
			e.Accuracy = &accuracy
		}
		if e.Samples > 0 {
			macro := 0.0
			for i, row := range matrix {
				support, predicted := 0, 0
				for j, n := range row {
					support += n
					predicted += matrix[j][i]
				}
				if support+predicted > 0 {
					macro += 2 * float64(row[i]) / float64(support+predicted)
				}
			}
			macro /= float64(len(matrix))
			e.MacroF1 = &macro
		}
		orderKnown := len(modelClasses) == len(matrix) && len(doc.Evaluation.PerClass) == len(matrix)
		seen := map[string]bool{}
		for i, label := range modelClasses {
			if !orderKnown {
				break
			}
			p, ok := doc.Evaluation.PerClass[label]
			if !ok || seen[label] {
				orderKnown = false
				break
			}
			seen[label] = true
			support := 0
			for _, n := range matrix[i] {
				support += n
			}
			if math.Abs(p.Support-float64(support)) > 0.001 {
				orderKnown = false
			}
		}
		if orderKnown {
			for i, label := range modelClasses {
				total := 0
				for j, n := range matrix[i] {
					total += n
					if i != j && n > 0 {
						e.Pairs = append(e.Pairs, Pair{True: label, Predicted: modelClasses[j], Count: n})
					}
				}
				errors := total - matrix[i][i]
				var rate *float64
				if total > 0 {
					v := float64(errors) / float64(total)
					rate = &v
				}
				var trainCount *int
				if n, ok := trainCounts[label]; ok {
					trainCount = &n
				}
				e.Classes = append(e.Classes, Class{Label: label, TestCount: total, ErrorCount: errors, ErrorRate: rate, TrainCount: trainCount})
			}
			support := map[string]int{}
			for _, c := range e.Classes {
				support[c.Label] = c.TestCount
			}
			for i := range e.Pairs {
				e.Pairs[i].Rate = float64(e.Pairs[i].Count) / float64(support[e.Pairs[i].True])
			}
			e.PairTotal = len(e.Pairs)
		} else {
			out.Limitations = append(out.Limitations, "缺少经核对的模型类别顺序，无法可靠给混淆矩阵标注类别，未猜测易混类别对。")
		}
		out.Availability = "aggregate_only"
		out.Limitations = append(out.Limitations, "历史报告没有逐样本预测与置信度，无法恢复具体错分样本或高置信错分统计。")
	}
	e := &out.Evidence
	if e.Samples > 0 && e.MacroF1 == nil && len(e.Classes) > 0 && e.PairTotal == len(e.Pairs) {
		predicted := map[string]int{}
		for _, c := range e.Classes {
			predicted[c.Label] = c.TestCount - c.ErrorCount
		}
		for _, p := range e.Pairs {
			predicted[p.Predicted] += p.Count
		}
		macro := 0.0
		for _, c := range e.Classes {
			denom := c.TestCount + predicted[c.Label]
			if denom > 0 {
				macro += 2 * float64(c.TestCount-c.ErrorCount) / float64(denom)
			}
		}
		macro /= float64(len(e.Classes))
		e.MacroF1 = &macro
	}
	if e.Samples == 0 {
		out.Availability = "unavailable"
		out.Limitations = append(out.Limitations, "该训练没有独立测试样本，不能用验证集替代。")
		return out, nil
	}
	sort.Slice(e.Pairs, func(i, j int) bool {
		a, b := e.Pairs[i], e.Pairs[j]
		if a.Count != b.Count {
			return a.Count > b.Count
		}
		if a.True != b.True {
			return a.True < b.True
		}
		return a.Predicted < b.Predicted
	})
	sort.Slice(e.Classes, func(i, j int) bool {
		a, b := e.Classes[i], e.Classes[j]
		if a.ErrorCount != b.ErrorCount {
			return a.ErrorCount > b.ErrorCount
		}
		return a.Label < b.Label
	})
	out.ClassTotal = len(e.Classes)
	out.ClassStatsTruncated = len(e.Classes) > 20
	if len(e.Classes) > 20 {
		e.Classes = e.Classes[:20]
	}
	if len(e.Pairs) > maxPairs {
		e.Pairs = e.Pairs[:maxPairs]
	}
	if len(e.Examples) > maxExamples {
		e.Examples = e.Examples[:maxExamples]
	}
	if e.Errors == 0 {
		out.Findings = append(out.Findings, Finding{Kind: "observed", Claim: "这次独立测试集没有观测到分类错误；不能据此证明模型在其他数据上也不会出错。", Evidence: map[string]int{"test_count": e.Samples}, NextStep: "扩大独立测试覆盖，保持训练与测试数据分离。"})
	}
	if len(e.Pairs) > 0 {
		out.Findings = append(out.Findings, Finding{Kind: "observed", Claim: "错误集中在哪些类别对可由混淆计数核实；类别视觉相似尚未验证。", Evidence: e.Pairs[0], NextStep: "对照原图检查类别边界、拍摄条件和标注标准。"})
	}
	if e.HighErrors != nil && *e.HighErrors > 0 {
		out.Findings = append(out.Findings, Finding{Kind: "hypothesis", Claim: "存在高置信错分，标注歧义、背景依赖或分布差异值得排查，但仅凭分数无法认定原因。", Evidence: map[string]any{"count": *e.HighErrors, "raw_softmax_threshold": e.HighThreshold}, NextStep: "优先人工复核高置信错分原图，另用验证集检查校准。"})
	}
	if e.LowErrors != nil && *e.LowErrors > 0 {
		out.Findings = append(out.Findings, Finding{Kind: "hypothesis", Claim: "部分错误的前两类概率接近，可能存在类别边界重叠；这不是视觉相似的直接证据。", Evidence: map[string]any{"count": *e.LowErrors, "top_two_margin_max": e.LowThreshold}, NextStep: "在训练或独立开发集补充易混类别的对照样本。"})
	}
	lowSupport := []string{}
	for _, c := range e.Classes {
		if c.ErrorCount > 0 && c.TrainCount != nil && *c.TrainCount < 10 {
			lowSupport = append(lowSupport, c.Label)
		}
	}
	if len(lowSupport) > 0 {
		out.Findings = append(out.Findings, Finding{Kind: "hypothesis", Claim: "返回的高错分类别中存在训练样本少于 10 个的类别，样本覆盖不足值得排查；这不能证明样本量是错误的原因。", Evidence: lowSupport, NextStep: "检查训练/开发集的类别覆盖，优先补充独立采集的代表性训练样本。"})
	}
	out.Limitations = append(out.Limitations, "本工具的 accuracy 和 macro_f1 只对应独立 test 划分；其他工具的训练概况指标若未核实相同划分，不得混用。", "本工具分析已保存的最终模型测试预测，不读取原图，也未验证遮挡、背景、图像质量或错标等视觉原因。", "错分示例按错误预测的原始 softmax 概率优先选取，最多保存 50 张；示例不代表全部错分分布。", "测试集用于最终评估与审计；根据这里的发现迭代后，应另留独立测试集，避免反复针对同一测试集调参。")
	return out, nil
}
func validate(e Evidence) error {
	if e.MacroF1 != nil && (*e.MacroF1 < 0 || *e.MacroF1 > 1) {
		return fmt.Errorf("invalid test macro-F1")
	}
	if e.Version != 1 || e.Split != "test" || e.Samples < 0 || e.Errors < 0 || e.Errors > e.Samples || len(e.Examples) > 50 || e.Retained != len(e.Examples) || len(e.Classes) > 2000 || len(e.Pairs) > 200 || e.PairTotal < len(e.Pairs) {
		return fmt.Errorf("invalid test evidence")
	}
	total, errors := 0, 0
	seen := map[string]bool{}
	for _, c := range e.Classes {
		if c.Label == "" || seen[c.Label] || c.TestCount < 0 || c.ErrorCount < 0 || c.ErrorCount > c.TestCount || (c.TrainCount != nil && *c.TrainCount < 0) {
			return fmt.Errorf("invalid class evidence")
		}
		seen[c.Label] = true
		total += c.TestCount
		errors += c.ErrorCount
	}
	if (e.HighErrors != nil && (*e.HighErrors < 0 || *e.HighErrors > e.Errors)) || (e.LowErrors != nil && (*e.LowErrors < 0 || *e.LowErrors > e.Errors)) || (e.Samples > 0 && e.Accuracy == nil) {
		return fmt.Errorf("invalid confidence or accuracy evidence")
	}
	if total != e.Samples || errors != e.Errors {
		return fmt.Errorf("test counts disagree")
	}
	if e.Accuracy != nil && (math.Abs(*e.Accuracy-float64(e.Samples-e.Errors)/float64(max(e.Samples, 1))) > 0.00001) {
		return fmt.Errorf("test accuracy disagrees")
	}
	pairCount := 0
	pairKeys := map[[2]string]bool{}
	for _, p := range e.Pairs {
		key := [2]string{p.True, p.Predicted}
		if pairKeys[key] {
			return fmt.Errorf("duplicate confusion pair")
		}
		pairKeys[key] = true
		pairCount += p.Count
		if !seen[p.True] || !seen[p.Predicted] || p.True == p.Predicted || p.Count < 1 || p.Count > e.Errors || p.Rate < 0 || p.Rate > 1 {
			return fmt.Errorf("invalid confusion pair")
		}
	}
	if pairCount > e.Errors || (e.PairTotal == len(e.Pairs) && pairCount != e.Errors) || len(e.Examples) > e.Errors {
		return fmt.Errorf("inconsistent retained error evidence")
	}
	ids := map[string]bool{}
	for _, x := range e.Examples {
		if ids[x.ID] || x.ID == "" || !seen[x.True] || !seen[x.Predicted] || x.True == x.Predicted || x.Confidence < 0 || x.Confidence > 1 || x.TrueProbability < 0 || x.TrueProbability > 1 || x.Margin < 0 || x.Margin > 1 {
			return fmt.Errorf("invalid test example")
		}
		ids[x.ID] = true
	}
	return nil
}
