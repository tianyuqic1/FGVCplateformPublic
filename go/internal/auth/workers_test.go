package auth

import "testing"

func TestWorkerReadPermissions(t *testing.T) {
	for _, path := range []string{"/api/workers", "/api/workers/training-1"} {
		if allowed(Annotator, "GET", path) {
			t.Fatal("annotator allowed worker inventory")
		}
		if !allowed(Business, "GET", path) || !allowed(Admin, "GET", path) {
			t.Fatal("reader blocked")
		}
	}
	for _, path := range []string{"/api/internal/workers/register", "/api/internal/workers/heartbeat"} {
		if !isServicePath(path) {
			t.Fatal("service path must use its own token handler")
		}
	}
	if isServicePath("/api/workers") {
		t.Fatal("public inventory bypasses login")
	}
	for _, method := range []string{"POST", "PATCH", "DELETE"} {
		if allowed(Business, method, "/api/workers/test") {
			t.Fatal("business worker mutation allowed")
		}
	}
}
