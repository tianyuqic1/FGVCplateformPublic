package openapi

import (
	"bufio"
	"bytes"
	"fmt"
	"regexp"
	"strings"
	"testing"
)

var operationLine = regexp.MustCompile(`^    (get|post|put|patch|delete):$`)

func TestEveryOperationHasABusinessTag(t *testing.T) {
	t.Parallel()

	for _, specification := range []struct {
		name          string
		minimumGroups int
	}{
		{name: "finevision.yaml", minimumGroups: 6},
		{name: "hardware.yaml", minimumGroups: 2},
	} {
		specification := specification
		t.Run(specification.name, func(t *testing.T) {
			t.Parallel()

			content, err := Specifications.ReadFile(specification.name)
			if err != nil {
				t.Fatal(err)
			}
			operations, groups, missing := inspectOperationTags(content)
			if len(missing) > 0 {
				t.Fatalf("%s has operations without business tags: %s", specification.name, strings.Join(missing, ", "))
			}
			if operations == 0 {
				t.Fatalf("%s has no operations", specification.name)
			}
			if len(groups) < specification.minimumGroups {
				t.Fatalf("%s has %d business groups, want at least %d", specification.name, len(groups), specification.minimumGroups)
			}
		})
	}
}

func inspectOperationTags(content []byte) (int, map[string]struct{}, []string) {
	scanner := bufio.NewScanner(bytes.NewReader(content))
	groups := make(map[string]struct{})
	var missing []string
	var path, operation string
	hasTag := false
	operations := 0
	finishOperation := func() {
		if operation != "" && !hasTag {
			missing = append(missing, fmt.Sprintf("%s %s", operation, path))
		}
		operation = ""
		hasTag = false
	}

	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "  /") && strings.HasSuffix(line, ":") {
			finishOperation()
			path = strings.TrimSuffix(strings.TrimSpace(line), ":")
			continue
		}
		if operationLine.MatchString(line) {
			finishOperation()
			operation = strings.TrimSuffix(strings.TrimSpace(line), ":")
			operations++
			continue
		}
		if operation != "" && strings.HasPrefix(line, "      tags: [") {
			hasTag = true
			value := strings.TrimSuffix(strings.TrimPrefix(strings.TrimSpace(line), "tags: ["), "]")
			for _, group := range strings.Split(value, ",") {
				groups[strings.TrimSpace(group)] = struct{}{}
			}
		}
	}
	finishOperation()
	return operations, groups, missing
}
