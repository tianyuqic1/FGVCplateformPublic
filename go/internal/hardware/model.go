package hardware

import (
	"fmt"
	"math"
	"regexp"
	"time"
)

type Memory struct {
	Used  *float64 `json:"used_bytes"`
	Total *float64 `json:"total_bytes"`
}
type CPU struct {
	Percent *float64 `json:"percent"`
	Cores   int      `json:"cores"`
}
type GPU struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	Percent     *float64 `json:"percent"`
	Memory      Memory   `json:"memory"`
	Temperature *float64 `json:"temperature_c"`
	Power       *float64 `json:"power_w"`
	PowerLimit  *float64 `json:"power_limit_w"`
}
type Disk struct {
	ID        string   `json:"id"`
	Paths     []string `json:"paths"`
	Memory    Memory   `json:"memory"`
	Available *float64 `json:"available_bytes"`
}
type Snapshot struct {
	NodeID     string    `json:"node_id"`
	Name       string    `json:"name"`
	Scope      string    `json:"scope"`
	SampledAt  time.Time `json:"sampled_at"`
	ReceivedAt time.Time `json:"received_at"`
	CPU        CPU       `json:"cpu"`
	Memory     Memory    `json:"memory"`
	GPUs       []GPU     `json:"gpus"`
	GPUStatus  string    `json:"gpu_status"`
	Disks      []Disk    `json:"disks"`
	Errors     []string  `json:"errors"`
}
type Task struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	StartedAt time.Time `json:"started_at"`
}
type Alert struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

var nodePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$`)

func ValidNode(id string) bool { return nodePattern.MatchString(id) }
func validValue(v *float64, max float64) bool {
	return v == nil || (!math.IsNaN(*v) && !math.IsInf(*v, 0) && *v >= 0 && *v <= max)
}
func validMemory(m Memory) bool {
	return validValue(m.Used, 1e18) && validValue(m.Total, 1e18) && (m.Used == nil || m.Total == nil || *m.Used <= *m.Total)
}
func (s Snapshot) Validate(now time.Time) error {
	if !ValidNode(s.NodeID) || len(s.Name) == 0 || len(s.Name) > 160 || s.Scope != "host" || s.SampledAt.Before(now.Add(-30*time.Second)) || s.SampledAt.After(now.Add(30*time.Second)) {
		return fmt.Errorf("invalid node, host scope, or sample timestamp (30s clock tolerance)")
	}
	if !validValue(s.CPU.Percent, 100) || s.CPU.Cores < 0 || s.CPU.Cores > 65536 || !validMemory(s.Memory) || len(s.GPUs) > 64 || len(s.Disks) > 64 || len(s.Errors) > 32 {
		return fmt.Errorf("invalid resource metrics")
	}
	if s.GPUStatus != "ok" && s.GPUStatus != "none" && s.GPUStatus != "unavailable" {
		return fmt.Errorf("invalid gpu_status")
	}
	if (s.GPUStatus == "ok") != (len(s.GPUs) > 0) {
		return fmt.Errorf("GPU inventory contradicts status")
	}
	ids := map[string]bool{}
	for _, g := range s.GPUs {
		if g.ID == "" || len(g.ID) > 160 || ids[g.ID] || len(g.Name) > 160 || !validValue(g.Percent, 100) || !validMemory(g.Memory) || !validValue(g.Temperature, 200) || !validValue(g.Power, 10000) || !validValue(g.PowerLimit, 10000) {
			return fmt.Errorf("invalid GPU metrics")
		}
		ids[g.ID] = true
	}
	ids = map[string]bool{}
	for _, d := range s.Disks {
		if d.ID == "" || len(d.ID) > 160 || ids[d.ID] || len(d.Paths) == 0 || len(d.Paths) > 32 || !validMemory(d.Memory) || !validValue(d.Available, 1e18) || (d.Available != nil && d.Memory.Total != nil && *d.Available > *d.Memory.Total) {
			return fmt.Errorf("invalid disk metrics")
		}
		ids[d.ID] = true
		for _, p := range d.Paths {
			if len(p) > 512 {
				return fmt.Errorf("disk path too long")
			}
		}
	}
	for _, e := range s.Errors {
		if len(e) > 256 {
			return fmt.Errorf("error label too long")
		}
	}
	return nil
}
func (s Snapshot) Status(now time.Time) string {
	age := now.Sub(s.ReceivedAt)
	if age > 60*time.Second {
		return "offline"
	}
	if age > 15*time.Second {
		return "stale"
	}
	return "online"
}
func pressure(s Snapshot) map[string]string {
	result := map[string]string{}
	if ratio(s.Memory) >= .9 {
		result["memory"] = "主机内存持续超过 90%，请关注训练与推理的并发占用。"
	}
	for _, g := range s.GPUs {
		if ratio(g.Memory) >= .95 {
			result["gpu:"+g.ID] = "GPU " + g.Name + " 显存持续超过 95%，新任务可能无法分配显存。"
		}
	}
	for _, d := range s.Disks {
		if d.Memory.Total != nil && *d.Memory.Total > 0 && d.Available != nil && *d.Available / *d.Memory.Total < .1 {
			result["disk:"+d.ID] = "磁盘 " + d.Paths[0] + " 可用空间持续不足 10%，可能影响数据导入和模型保存。"
		}
	}
	return result
}
func ratio(m Memory) float64 {
	if m.Used == nil || m.Total == nil || *m.Total <= 0 {
		return -1
	}
	return *m.Used / *m.Total
}

// Alerts require 60 seconds of continuous pressure, with no sampling gap >15 seconds.
func Alerts(samples []Snapshot, now time.Time) []Alert {
	alerts := []Alert{}
	if len(samples) < 2 || samples[len(samples)-1].Status(now) != "online" {
		return alerts
	}
	latest := samples[len(samples)-1]
	for code, message := range pressure(latest) {
		start := latest.ReceivedAt
		for i := len(samples) - 2; i >= 0; i-- {
			if start.Sub(samples[i].ReceivedAt) > 15*time.Second {
				break
			}
			if _, ok := pressure(samples[i])[code]; !ok {
				break
			}
			start = samples[i].ReceivedAt
		}
		if latest.ReceivedAt.Sub(start) >= 60*time.Second {
			alerts = append(alerts, Alert{code, message})
		}
	}
	return alerts
}
