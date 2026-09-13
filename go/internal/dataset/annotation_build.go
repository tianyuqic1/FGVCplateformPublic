package dataset

import (
	"archive/zip"
	"context"
	"crypto/sha256"
	"fmt"
	"os"
	"path"
	"sort"

	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/artifact"
)

type LabeledImage struct {
	ID       string              `json:"id"`
	Label    string              `json:"label"`
	Filename string              `json:"filename"`
	Image    artifact.Descriptor `json:"image"`
}
type SplitPlan struct {
	Train int `json:"train"`
	Val   int `json:"val"`
	Test  int `json:"test"`
	Seed  int `json:"seed"`
}

func (p SplitPlan) Validate() error {
	if p.Train < 1 || p.Train > 100 || p.Val < 0 || p.Val > 100 || p.Test < 0 || p.Test > 100 || p.Train+p.Val+p.Test != 100 {
		return fmt.Errorf("%w: 训练比例必须大于 0，三个整数比例之和必须为 100", ErrInvalid)
	}
	return nil
}

// SplitLabels uses largest remainders per class, with at least one training
// sample. The caller orders each class by seed+SHA, not upload order.
func SplitLabels(n int, p SplitPlan) [3]int {
	counts := [3]int{}
	weights := [3]int{p.Train, p.Val, p.Test}
	left := n
	for i, w := range weights {
		counts[i] = n * w / 100
		left -= counts[i]
	}
	order := []int{0, 1, 2}
	sort.SliceStable(order, func(i, j int) bool { return n*weights[order[i]]%100 > n*weights[order[j]]%100 })
	for i := 0; i < left; i++ {
		counts[order[i]]++
	}
	if n > 0 && counts[0] == 0 {
		for _, i := range []int{1, 2} {
			if counts[i] > 0 {
				counts[i]--
				counts[0]++
				break
			}
		}
	}
	return counts
}

// BuildLabeledArchive preserves every baseline label/split. Only new images are
// split. Cross-label duplicates and evaluation leakage block the entire batch.
func (s *Service) BuildLabeledArchive(ctx context.Context, base *Snapshot, images []LabeledImage, split SplitPlan, trainOnly bool) (string, map[string]any, error) {
	if err := split.Validate(); err != nil {
		return "", nil, err
	}
	if len(images) == 0 || len(images) > 10000 {
		return "", nil, ErrInvalid
	}
	cache, err := os.MkdirTemp("", "annotation-build-*")
	if err != nil {
		return "", nil, err
	}
	defer os.RemoveAll(cache)
	file, err := os.CreateTemp("", "annotation-release-*.zip")
	if err != nil {
		return "", nil, err
	}
	success := false
	defer func() {
		file.Close()
		if !success {
			os.Remove(file.Name())
		}
	}()
	writer := zip.NewWriter(file)
	defer writer.Close()
	type identity struct{ label, split string }
	seen := map[string]identity{}
	counts := map[string]map[string]int{"train": {}, "val": {}, "test": {}}
	var total int64
	count, duplicates := 0, 0
	write := func(name, label, partition string, b []byte) error {
		count++
		total += int64(len(b))
		if count > MaxUploadImages || total > MaxUploadBytes {
			return ErrInvalidArchive
		}
		w, e := writer.CreateHeader(&zip.FileHeader{Name: name, Method: zip.Store})
		if e != nil {
			return e
		}
		if _, e = w.Write(b); e != nil {
			return e
		}
		counts[partition][label]++
		return nil
	}
	existingLabels := map[string]bool{}
	if base != nil {
		p, e := s.Store.MaterializeVerified(ctx, base.Archive, cache)
		if e != nil {
			return "", nil, e
		}
		z, e := zip.OpenReader(p)
		if e != nil {
			return "", nil, e
		}
		defer z.Close()
		members := map[string]*zip.File{}
		for _, f := range z.File {
			members[f.Name] = f
		}
		samples, ok := base.Manifest["samples"].([]any)
		if !ok || len(samples) == 0 {
			return "", nil, ErrInvalidArchive
		}
		for i, raw := range samples {
			if e := ctx.Err(); e != nil {
				return "", nil, e
			}
			row, ok := raw.(map[string]any)
			if !ok {
				return "", nil, ErrInvalidArchive
			}
			original, _ := row["path"].(string)
			label, _ := row["label"].(string)
			partition, _ := row["split"].(string)
			if !safePath(original) || !safePart(label) || counts[partition] == nil || members[original] == nil {
				return "", nil, ErrInvalidArchive
			}
			r, e := members[original].Open()
			if e != nil {
				return "", nil, e
			}
			b, e := readImage(r)
			r.Close()
			if e != nil {
				return "", nil, e
			}
			digest := fmt.Sprintf("%x", sha256.Sum256(b))
			// Existing datasets are retained exactly, even if they contain legacy duplicates.
			prior, ok := seen[digest]
			if ok && prior.label != label {
				labelConflict := prior
				labelConflict.label = ""
				seen[digest] = labelConflict
			} else if !ok || partition != "train" {
				seen[digest] = identity{label, partition}
			}
			existingLabels[label] = true
			if e = write(fmt.Sprintf("%s/%s/base-%06d%s", partition, label, i, path.Ext(original)), label, partition, b); e != nil {
				return "", nil, e
			}
		}
	}
	type pending struct {
		sample                   LabeledImage
		digest, local, partition string
	}
	groups := map[string][]pending{}
	var incomingBytes int64
	for _, sample := range images {
		if !safePart(sample.Label) || !imageExtension(sample.Filename) {
			return "", nil, fmt.Errorf("%w: 类别名称不能含路径分隔符，图片须为受支持格式", ErrInvalid)
		}
		if sample.Image.SizeBytes <= 0 || sample.Image.SizeBytes > maxImageBytes || len(sample.Image.SHA256) != 64 {
			return "", nil, ErrInvalidArchive
		}
		incomingBytes += sample.Image.SizeBytes
		if incomingBytes+total > MaxUploadBytes {
			return "", nil, fmt.Errorf("%w: 本批与基准版本总量超过 5 GB", ErrInvalid)
		}
		p, e := s.Store.MaterializeVerified(ctx, sample.Image, cache)
		if e != nil {
			return "", nil, e
		}
		digest := sample.Image.SHA256
		if old, ok := seen[digest]; ok {
			if old.label != sample.Label {
				return "", nil, fmt.Errorf("%w: 图片与已有标签冲突；已注册标签不允许修改", ErrInvalid)
			}
			if old.split == "val" || old.split == "test" {
				return "", nil, fmt.Errorf("%w: 图片已在验证集或测试集中，禁止重复加入", ErrInvalid)
			}
			duplicates++
			continue
		}
		seen[digest] = identity{sample.Label, "new"}
		groups[sample.Label] = append(groups[sample.Label], pending{sample: sample, digest: digest, local: p})
	}
	labels := []string{}
	for label := range groups {
		labels = append(labels, label)
	}
	sort.Strings(labels)
	added := 0
	sources := map[string]any{}
	warnings := []string{}
	newClasses := []string{}
	existingAdditions, newAdditions := map[string]int{}, map[string]int{}
	for _, label := range labels {
		list := groups[label]
		if existingLabels[label] {
			existingAdditions[label] = len(list)
		} else {
			newClasses = append(newClasses, label)
			newAdditions[label] = len(list)
		}
		sort.Slice(list, func(i, j int) bool {
			a := sha256.Sum256([]byte(fmt.Sprintf("%d:%s", split.Seed, list[i].digest)))
			b := sha256.Sum256([]byte(fmt.Sprintf("%d:%s", split.Seed, list[j].digest)))
			return string(a[:]) < string(b[:])
		})
		allocation := SplitLabels(len(list), split)
		if trainOnly {
			allocation = [3]int{len(list), 0, 0}
		}
		if !trainOnly && ((split.Val > 0 && allocation[1] == 0) || (split.Test > 0 && allocation[2] == 0)) {
			warnings = append(warnings, fmt.Sprintf("%s 样本较少，无法覆盖所有指定集合", label))
		}
		for i, item := range list {
			partition := "test"
			if i < allocation[0] {
				partition = "train"
			} else if i < allocation[0]+allocation[1] {
				partition = "val"
			}
			r, e := os.Open(item.local)
			if e != nil {
				return "", nil, e
			}
			b, e := readImage(r)
			r.Close()
			if e != nil {
				return "", nil, e
			}
			name := fmt.Sprintf("%s/%s/%s%s", partition, label, item.digest, path.Ext(item.sample.Filename))
			if e = write(name, label, partition, b); e != nil {
				return "", nil, e
			}
			added++
			sources[name] = map[string]string{"annotation_task_id": item.sample.ID, "sha256": item.digest}
		}
	}
	if added == 0 {
		return "", nil, fmt.Errorf("%w: 去重后没有可新增的图片", ErrInvalid)
	}
	if err = writer.Close(); err != nil {
		return "", nil, err
	}
	if err = file.Close(); err != nil {
		return "", nil, err
	}
	success = true
	return file.Name(), map[string]any{"added_count": added, "duplicate_count": duplicates, "split_counts": counts, "split_plan": split, "train_only": trainOnly, "sample_sources": sources, "warnings": warnings, "labels_immutable": true, "new_classes": newClasses, "existing_class_additions": existingAdditions, "new_class_additions": newAdditions}, nil
}
