// agent-tools exercises the tool layer without starting an Agent or LLM.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/toolcontract"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/adapters/platform"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/internal/tools"
)

func main() {
	if err := run(os.Args[1:], os.Stdin, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run(args []string, input io.Reader, output io.Writer) error {
	if len(args) == 1 && args[0] == "list" {
		return json.NewEncoder(output).Encode(tools.Definitions())
	}
	if len(args) == 1 && args[0] == "openapi" {
		return json.NewEncoder(output).Encode(toolcontract.OpenAPI())
	}
	if len(args) == 0 {
		return fmt.Errorf("usage: agent-tools list | invoke|approve|operation [options]; see agent/docs/tools.md")
	}
	mode := args[0]
	flags := flag.NewFlagSet("agent-tools", flag.ContinueOnError)
	base := flags.String("platform", "http://localhost:8001", "platform origin")
	sessionFile := flags.String("session-file", "", "0600 JSON file containing cookie and csrf; never put secrets in command arguments")
	opID := flags.String("operation-id", "", "stable write operation UUID")
	approvalFile := flags.String("approval-file", "", "0600 JSON file returned by approve")
	confirm := flags.Bool("confirm", false, "explicitly approve the supplied action as the authenticated user")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if mode != "invoke" && mode != "approve" && mode != "operation" {
		return fmt.Errorf("unsupported command")
	}
	if *sessionFile == "" {
		return fmt.Errorf("session-file is required")
	}
	var session platform.Session
	if err := readPrivate(*sessionFile, &session); err != nil {
		return err
	}
	client, err := platform.New(*base, nil)
	if err != nil {
		return err
	}
	ctx := platform.WithSession(context.Background(), session)
	if mode == "operation" {
		v, err := client.Operation(ctx, *opID)
		if err != nil {
			return err
		}
		return json.NewEncoder(output).Encode(v)
	}
	var call struct {
		Tool      string          `json:"tool"`
		Arguments json.RawMessage `json:"arguments"`
	}
	d := json.NewDecoder(io.LimitReader(input, (64<<10)+1))
	d.DisallowUnknownFields()
	if d.Decode(&call) != nil || d.Decode(new(any)) != io.EOF {
		return fmt.Errorf("stdin must contain one tool/arguments object")
	}
	registry, _ := tools.New(client, 8, 4)
	if _, err := registry.Validate(call.Tool, call.Arguments); err != nil {
		return err
	}
	if mode == "approve" {
		if !*confirm {
			return fmt.Errorf("approve requires explicit --confirm; show normalized preview to the user first")
		}
		v, err := client.Approve(ctx, call.Tool, call.Arguments, *opID)
		if err != nil {
			return err
		}
		return json.NewEncoder(output).Encode(v)
	}
	control := tools.Control{OperationID: *opID}
	if *approvalFile != "" {
		var approval platform.ApprovedAction
		if err := readPrivate(*approvalFile, &approval); err != nil {
			return err
		}
		control.Approval = approval.Approval
		control.OperationID = approval.OperationID
		// Approval binds exactly these normalized parameters. Do not silently
		// substitute a different stdin action or operation identifier.
		if *opID != "" && *opID != approval.OperationID {
			return fmt.Errorf("operation ID differs from approval")
		}
	}
	v, err := registry.Execute(ctx, call.Tool, call.Arguments, control)
	if err != nil {
		return err
	}
	return json.NewEncoder(output).Encode(v)
}
func readPrivate(path string, out any) error {
	info, err := os.Stat(path)
	if err != nil {
		return fmt.Errorf("cannot read credentials file")
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 16<<10 {
		return fmt.Errorf("credentials and approval files must be regular, <=16 KiB and private (0600)")
	}
	f, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("cannot read credentials file")
	}
	defer f.Close()
	d := json.NewDecoder(f)
	if d.Decode(out) != nil || d.Decode(new(any)) != io.EOF {
		return fmt.Errorf("invalid credentials file JSON")
	}
	return nil
}
