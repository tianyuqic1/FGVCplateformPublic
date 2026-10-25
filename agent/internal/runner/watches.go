package runner

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/delegation"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/adapters/platform"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/tools"
	"time"
)

// WatchLoop polls business state without holding a Run or model connection.
func (r *Runner) WatchLoop(ctx context.Context) {
	for ctx.Err() == nil {
		w, e := r.Store.ClaimWatch(ctx)
		if e != nil || w.ID == "" {
			select {
			case <-ctx.Done():
				return
			case <-time.After(time.Second):
			}
			continue
		}
		if _, e = delegation.Verify(r.Key, w.Grant, time.Now()); e != nil {
			_ = r.Store.SaveWatch(ctx, w, "waiting_auth", w.ObservedStatus, "跟踪授权已过期，请恢复授权", w.Result)
			continue
		}
		args, _ := json.Marshal(map[string]string{"run_id": w.TrainingRunID})
		toolctx := platform.WithSession(ctx, platform.Session{Delegation: w.Grant})
		out, e := r.Registry.Execute(toolctx, "training.get_run", args, tools.Control{})
		if e != nil {
			status := "active"
			msg := "平台查询暂不可用，将在 30 秒后重查"
			var te *tools.Error
			if errors.As(e, &te) && (te.Status == 401 || te.Status == 403) {
				status = "waiting_auth"
				msg = "授权已过期或权限变化，请恢复授权"
			}
			if errors.As(e, &te) && te.Status == 404 {
				status = "completed"
				msg = "训练记录已删除，跟踪结束"
			}
			_ = r.Store.SaveWatch(ctx, w, status, w.ObservedStatus, msg, w.Result)
			continue
		}
		var payload struct {
			TrainingRun struct {
				Status string `json:"status"`
			} `json:"training_run"`
		}
		if json.Unmarshal(out.Data, &payload) != nil || payload.TrainingRun.Status == "" {
			_ = r.Store.SaveWatch(ctx, w, "active", w.ObservedStatus, "平台返回了无效状态", w.Result)
			continue
		}
		observed := payload.TrainingRun.Status
		status := "active"
		if observed == "succeeded" || observed == "failed" || observed == "cancelled" {
			status = "completed"
		}
		_ = r.Store.SaveWatch(ctx, w, status, observed, "", out.Data)
	}
}
