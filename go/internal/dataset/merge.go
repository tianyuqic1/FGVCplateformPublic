package dataset

import (
	"archive/zip"
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"
)

const maxImageBytes = 32 << 20
const maxDatasetBytes = 512 << 20

func safePart(s string) bool {
	return s != "" && s != "." && s != ".." && !strings.ContainsAny(s, "/\\")
}
func safePath(s string) bool {
	if strings.HasPrefix(s, "/") || strings.Contains(s, "\\") {
		return false
	}
	for _, p := range strings.Split(s, "/") {
		if !safePart(p) {
			return false
		}
	}
	return true
}
func imageExtension(s string) bool {
	switch strings.ToLower(path.Ext(s)) {
	case ".jpg", ".jpeg", ".png", ".webp", ".bmp":
		return true
	}
	return false
}
func readImage(r io.Reader) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(r, maxImageBytes+1))
	if err != nil {
		return nil, err
	}
	if len(b) == 0 || len(b) > maxImageBytes {
		return nil, ErrInvalidArchive
	}
	return b, nil
}

// Rebuild with explicit split directories from the *published manifest*. Never
// resplit the old class folders when appending samples. The training worker can
// consequently scan the archive without changing any evaluation membership.
func (s *Service) merge(ctx context.Context, base Snapshot, incoming string, feedback []Feedback) (output string, changes map[string]any, err error) {
	cache, err := os.MkdirTemp("", "dataset-merge-cache-*")
	if err != nil {
		return
	}
	defer os.RemoveAll(cache)
	basePath, err := s.Store.MaterializeVerified(ctx, base.Archive, cache)
	if err != nil {
		return
	}
	original, err := zip.OpenReader(basePath)
	if err != nil {
		return
	}
	defer original.Close()
	members := map[string]*zip.File{}
	for _, f := range original.File {
		if !safePath(f.Name) {
			return "", nil, ErrInvalidArchive
		}
		members[f.Name] = f
	}
	file, err := os.CreateTemp("", "dataset-expanded-*.zip")
	if err != nil {
		return
	}
	output = file.Name()
	temporaryPath := output
	defer func() {
		file.Close()
		if err != nil {
			os.Remove(temporaryPath)
		}
	}()
	writer := zip.NewWriter(file)
	defer writer.Close()
	seen := map[string]string{}
	names := map[string]bool{}
	count, total, added, duplicates := 0, 0, 0, 0
	sources := map[string]any{}
	write := func(name, label string, b []byte, addition bool, source any) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		digest := fmt.Sprintf("%x", sha256.Sum256(b))
		if previous, ok := seen[digest]; ok && addition {
			if previous != label {
				return fmt.Errorf("%w: 同一图片存在不同标签，请先处理标签冲突", ErrInvalid)
			}
			duplicates++
			return nil
		}
		if !safePath(name) || names[name] {
			return ErrInvalidArchive
		}
		count++
		total += len(b)
		if count > 10000 || total > maxDatasetBytes {
			return fmt.Errorf("%w: 合并后上限为 10000 张、512 MiB", ErrInvalid)
		}
		out, e := writer.Create(name)
		if e != nil {
			return e
		}
		if _, e = out.Write(b); e != nil {
			return e
		}
		// A conflicting legacy duplicate must still prevent new additions with either label.
		if old, ok := seen[digest]; ok && old != label {
			seen[digest] = ""
		} else if !ok {
			seen[digest] = label
		}
		names[name] = true
		if addition {
			added++
			sources[name] = source
		}
		return nil
	}
	samples, ok := base.Manifest["samples"].([]any)
	if !ok || len(samples) == 0 {
		return "", nil, fmt.Errorf("%w: 基础版本缺少样本清单，无法保证测试集不变", ErrInvalid)
	}
	for i, raw := range samples {
		sample, ok := raw.(map[string]any)
		if !ok {
			return "", nil, ErrInvalidArchive
		}
		oldPath, _ := sample["path"].(string)
		label, _ := sample["label"].(string)
		split, _ := sample["split"].(string)
		if !safePart(label) || (split != "train" && split != "val" && split != "test") {
			return "", nil, ErrInvalidArchive
		}
		member := members[oldPath]
		if member == nil {
			return "", nil, ErrInvalidArchive
		}
		r, e := member.Open()
		if e != nil {
			return "", nil, e
		}
		b, e := readImage(r)
		r.Close()
		if e != nil {
			return "", nil, e
		}
		name := oldPath
		if !strings.HasPrefix(oldPath, split+"/"+label+"/") {
			name = fmt.Sprintf("%s/%s/base-%06d%s", split, label, i, strings.ToLower(path.Ext(oldPath)))
		}
		if e = write(name, label, b, false, nil); e != nil {
			return "", nil, e
		}
	}
	add := func(label, filename string, b []byte, source any) error {
		if !safePart(label) || !imageExtension(filename) {
			return ErrInvalidArchive
		}
		name := fmt.Sprintf("train/%s/add-%x%s", label, sha256.Sum256(b), strings.ToLower(path.Ext(filename)))
		return write(name, label, b, true, source)
	}
	if incoming != "" {
		archive, e := zip.OpenReader(incoming)
		if e != nil {
			return "", nil, e
		}
		defer archive.Close()
		for _, member := range archive.File {
			if !safePath(member.Name) || member.FileInfo().IsDir() {
				return "", nil, ErrInvalidArchive
			}
			parts := strings.Split(member.Name, "/")
			if len(parts) < 2 {
				return "", nil, ErrInvalidArchive
			}
			if parts[0] == "val" || parts[0] == "test" {
				return "", nil, fmt.Errorf("%w: 本次仅支持扩充训练集，请移除 val/test 文件", ErrInvalid)
			}
			label := parts[0]
			if parts[0] == "train" {
				if len(parts) < 3 {
					return "", nil, ErrInvalidArchive
				}
				label = parts[1]
			}
			r, e := member.Open()
			if e != nil {
				return "", nil, e
			}
			b, e := readImage(r)
			r.Close()
			if e != nil {
				return "", nil, e
			}
			if e = add(label, member.Name, b, map[string]any{"type": "manual_upload", "original_path": member.Name}); e != nil {
				return "", nil, e
			}
		}
	}
	for _, f := range feedback {
		var b []byte
		if f.ImageArtifact != nil {
			p, e := s.Store.MaterializeVerified(ctx, *f.ImageArtifact, cache)
			if e != nil {
				return "", nil, e
			}
			r, e := os.Open(p)
			if e != nil {
				return "", nil, e
			}
			b, e = readImage(r)
			r.Close()
			if e != nil {
				return "", nil, e
			}
		} else {
			if s.UploadRoot == "" {
				return "", nil, fmt.Errorf("%w: 复核图片未存入可读取的对象存储，请配置复核上传目录", ErrInvalid)
			}
			rootPath, e := filepath.Abs(s.UploadRoot)
			if e != nil {
				return "", nil, e
			}
			inputPath, e := filepath.Abs(f.InputRef)
			if e != nil {
				return "", nil, e
			}
			rel, e := filepath.Rel(rootPath, inputPath)
			if e != nil || !filepath.IsLocal(rel) {
				return "", nil, fmt.Errorf("%w: 复核图片不在上传目录中", ErrInvalid)
			}
			root, e := os.OpenRoot(rootPath)
			if e != nil {
				return "", nil, e
			}
			r, e := root.Open(rel)
			if e != nil {
				root.Close()
				return "", nil, fmt.Errorf("%w: 复核原图不可读取", ErrInvalid)
			}
			b, e = readImage(r)
			r.Close()
			root.Close()
			if e != nil {
				return "", nil, e
			}
		}
		filename := f.InputRef
		if f.ImageArtifact != nil && !imageExtension(filename) {
			switch f.ImageArtifact.ContentType {
			case "image/png":
				filename = "review.png"
			case "image/jpeg":
				filename = "review.jpg"
			case "image/webp":
				filename = "review.webp"
			case "image/bmp":
				filename = "review.bmp"
			}
		}
		if e := add(f.Label, filename, b, f); e != nil {
			return "", nil, e
		}
	}
	if added == 0 {
		return "", nil, fmt.Errorf("%w: 没有新增图片，所选图片已存在于数据集中", ErrInvalid)
	}
	if err = writer.Close(); err != nil {
		return
	}
	if err = file.Close(); err != nil {
		return
	}
	changes = map[string]any{"added_count": added, "duplicate_count": duplicates, "train_only": true, "evaluation_preserved": true, "sample_sources": sources}
	return
}
