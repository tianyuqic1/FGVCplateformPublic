package auth

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Run against an isolated, migrated PostgreSQL database with FINEVISION_AUTH_TEST_DATABASE_URL.
func TestPostgresAccountLifecycle(t *testing.T) {
	dsn := os.Getenv("FINEVISION_AUTH_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("set FINEVISION_AUTH_TEST_DATABASE_URL to an isolated migrated test database")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	if !strings.Contains(strings.ToLower(pool.Config().ConnConfig.Database), "test") {
		t.Fatal("auth integration test refuses a database without 'test' in its name")
	}
	if err := pool.Ping(ctx); err != nil {
		t.Fatal(err)
	}
	store := &PostgresStore{Pool: pool}
	service := New(store)
	const password = "a sufficiently long test password"
	hash, err := HashPassword(password)
	if err != nil {
		t.Fatal(err)
	}
	suffix := uuid.NewString()
	adminEmail := fmt.Sprintf("admin-%s@example.com", suffix)
	userEmail := fmt.Sprintf("annotator-%s@example.com", suffix)
	if err := store.BootstrapAdmin(ctx, adminEmail, "Auth Test Admin", hash); err != nil {
		t.Fatal(err)
	}
	admin, _, _, err := service.Login(ctx, adminEmail, password)
	if err != nil || admin.Role != Admin {
		t.Fatalf("administrator login: %#v, %v", admin, err)
	}
	user, err := service.Register(ctx, userEmail, "Test Annotator", password)
	if err != nil || user.Status != Pending {
		t.Fatalf("registration: %#v, %v", user, err)
	}
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(), `DELETE FROM app_user_events WHERE user_id IN ($1,$2) OR actor_id IN ($1,$2)`, admin.ID, user.ID)
		_, _ = pool.Exec(context.Background(), `DELETE FROM app_users WHERE id IN ($1,$2)`, admin.ID, user.ID)
	})
	if _, _, _, err := service.Login(ctx, user.Email, password); !errors.Is(err, ErrPending) {
		t.Fatalf("pending account login: %v", err)
	}
	user, err = store.UpdateUser(ctx, admin.ID, user.ID, Annotator, Active, "integration test approval")
	if err != nil || user.Role != Annotator || user.Status != Active {
		t.Fatalf("approve: %#v, %v", user, err)
	}
	listed, total, err := store.ListUsers(ctx, 20, 0, userEmail, Active)
	if err != nil || total != 1 || len(listed) != 1 || listed[0].ID != user.ID {
		t.Fatalf("filtered users: %#v, %d, %v", listed, total, err)
	}
	_, secret, _, err := service.Login(ctx, user.Email, password)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := service.Session(ctx, secret); err != nil {
		t.Fatalf("created session: %v", err)
	}
	if _, err := store.UpdateUser(ctx, admin.ID, user.ID, Annotator, Disabled, "integration test disable"); err != nil {
		t.Fatal(err)
	}
	if _, err := service.Session(ctx, secret); !errors.Is(err, ErrCredentials) {
		t.Fatalf("session after disable: %v", err)
	}
	var activeAdmins int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM app_users WHERE role='admin' AND status='active'`).Scan(&activeAdmins); err != nil {
		t.Fatal(err)
	}
	if activeAdmins == 1 {
		if _, err := store.UpdateUser(ctx, user.ID, admin.ID, Business, Disabled, "last admin attempt"); !errors.Is(err, ErrLastAdmin) {
			t.Fatalf("last admin guard: %v", err)
		}
	}
	var events int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM app_user_events WHERE user_id=$1`, user.ID).Scan(&events); err != nil || events != 2 {
		t.Fatalf("audit events: %d, %v", events, err)
	}
}
