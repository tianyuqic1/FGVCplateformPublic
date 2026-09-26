// finevision-admin bootstraps or resets a local administrator from a one-shot command.
package main

import (
	"bufio"
	"context"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/auth"
	"golang.org/x/term"
)

func main() {
	emailFlag := flag.String("email", "", "administrator email")
	nameFlag := flag.String("name", "", "administrator display name")
	flag.Parse()
	email, err := auth.NormalizeEmail(*emailFlag)
	if err != nil || strings.TrimSpace(*nameFlag) == "" {
		fmt.Fprintln(os.Stderr, "provide --email and --name")
		os.Exit(2)
	}
	fmt.Fprint(os.Stderr, "Administrator password (at least 12 characters): ")
	var password string
	if term.IsTerminal(int(os.Stdin.Fd())) {
		value, readErr := term.ReadPassword(int(os.Stdin.Fd()))
		fmt.Fprintln(os.Stderr)
		password, err = string(value), readErr
	} else {
		password, err = bufio.NewReader(os.Stdin).ReadString('\n')
		password = strings.TrimRight(password, "\r\n")
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "password input failed")
		os.Exit(2)
	}
	hash, err := auth.HashPassword(password)
	password = ""
	if err != nil {
		fmt.Fprintln(os.Stderr, "password must contain 12–1024 bytes")
		os.Exit(2)
	}
	databaseURL := os.Getenv("FINEVISION_DATABASE_URL")
	if databaseURL == "" {
		databaseURL = os.Getenv("DATABASE_URL")
	}
	if databaseURL == "" {
		fmt.Fprintln(os.Stderr, "database URL is not configured")
		os.Exit(2)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		fmt.Fprintln(os.Stderr, "database connection failed")
		os.Exit(1)
	}
	defer pool.Close()
	if err = (&auth.PostgresStore{Pool: pool}).BootstrapAdmin(ctx, email, strings.TrimSpace(*nameFlag), hash); err != nil {
		fmt.Fprintln(os.Stderr, "administrator setup failed:", err)
		os.Exit(1)
	}
	fmt.Fprintln(os.Stdout, "Administrator account is active; any existing sessions were revoked.")
}
