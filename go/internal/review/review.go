package review

import (
	"context"
	"errors"
	"strings"
)

var ErrNotFound = errors.New("记录不存在")
var ErrConflict = errors.New("当前状态不允许此操作，请刷新页面")
var ErrInvalid = errors.New("请求参数无效")

type Filter struct {
	Status, Destination, Dataset string
	Limit, Offset                int
}
type Submission struct {
	Outcome     string `json:"final_outcome"`
	Destination string `json:"destination"`
	Label       string `json:"final_label"`
	Note        string `json:"reviewer_note"`
	Reviewer    string `json:"reviewer"`
}

func (s *Submission) Validate() error {
	s.Label = strings.TrimSpace(s.Label)
	allowed := map[string]string{"confirmed_label": "training_candidate", "corrected_label": "training_candidate", "ood": "ood_stress", "bad_image": "bad_image", "uncertain": "taxonomy_dispute", "ignore": "ignore"}
	dest, ok := allowed[s.Outcome]
	if !ok || (s.Destination != dest && !(s.Outcome == "uncertain" && s.Destination == "ignore")) {
		return ErrInvalid
	}
	if (s.Outcome == "confirmed_label" || s.Outcome == "corrected_label") && s.Label == "" {
		return ErrInvalid
	}
	if len(s.Label) > 512 || len(s.Note) > 8192 || len(s.Reviewer) > 256 {
		return ErrInvalid
	}
	return nil
}

type Repository interface {
	List(context.Context, Filter, bool) ([]map[string]any, int, error)
	Get(context.Context, string) (map[string]any, error)
	Submit(context.Context, string, Submission) (map[string]any, error)
	SaveAssistance(context.Context, string, map[string]any) error
}
