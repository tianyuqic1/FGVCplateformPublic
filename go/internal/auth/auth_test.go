package auth

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type memoryStore struct {
	mu       sync.Mutex
	accounts map[string]Account
	sessions map[string]Session
}

func newMemoryStore() *memoryStore {
	return &memoryStore{accounts: make(map[string]Account), sessions: make(map[string]Session)}
}

func (store *memoryStore) CreateUser(_ context.Context, user User, hash string) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	if _, exists := store.accounts[user.Email]; exists {
		return ErrDuplicate
	}
	store.accounts[user.Email] = Account{User: user, PasswordHash: hash}
	return nil
}

func (store *memoryStore) FindByEmail(_ context.Context, email string) (Account, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	account, ok := store.accounts[email]
	if !ok {
		return Account{}, ErrNotFound
	}
	return account, nil
}

func (store *memoryStore) RecordFailedLogin(_ context.Context, id uuid.UUID) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	for email, account := range store.accounts {
		if account.ID == id {
			account.FailedLogins++
			store.accounts[email] = account
			break
		}
	}
	return nil
}

func (store *memoryStore) ClearFailedLogins(_ context.Context, id uuid.UUID) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	for email, account := range store.accounts {
		if account.ID == id {
			account.FailedLogins = 0
			store.accounts[email] = account
			break
		}
	}
	return nil
}

func (store *memoryStore) CreateSession(_ context.Context, hash []byte, userID uuid.UUID, csrf string, _ time.Time) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	for _, account := range store.accounts {
		if account.ID == userID {
			store.sessions[string(hash)] = Session{User: account.User, CSRFToken: csrf}
			return nil
		}
	}
	return ErrNotFound
}

func (store *memoryStore) FindSession(_ context.Context, hash []byte) (Session, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	session, ok := store.sessions[string(hash)]
	if !ok {
		return Session{}, ErrCredentials
	}
	for _, account := range store.accounts {
		if account.ID == session.User.ID && account.Status == Active {
			session.User = account.User
			return session, nil
		}
	}
	return Session{}, ErrCredentials
}

func (store *memoryStore) DeleteSession(_ context.Context, hash []byte) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	delete(store.sessions, string(hash))
	return nil
}

func (store *memoryStore) ListUsers(_ context.Context, _, _ int, _ string, _ Status) ([]User, int, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	users := make([]User, 0, len(store.accounts))
	for _, a := range store.accounts {
		users = append(users, a.User)
	}
	return users, len(users), nil
}

func (store *memoryStore) UpdateUser(_ context.Context, actorID, userID uuid.UUID, role Role, status Status, _ string) (User, error) {
	if actorID == userID {
		return User{}, ErrInvalid
	}
	store.mu.Lock()
	defer store.mu.Unlock()
	for email, account := range store.accounts {
		if account.ID == userID {
			account.Role, account.Status = role, status
			store.accounts[email] = account
			for hash, session := range store.sessions {
				if session.User.ID == userID {
					delete(store.sessions, hash)
				}
			}
			return account.User, nil
		}
	}
	return User{}, ErrNotFound
}

func TestPasswordHashAndPendingRegistration(t *testing.T) {
	hash, err := HashPassword("correct horse battery staple")
	if err != nil || !VerifyPassword(hash, "correct horse battery staple") || VerifyPassword(hash, "incorrect password") || VerifyPassword("malformed", "anything") {
		t.Fatalf("password verification failed: %v", err)
	}
	if _, err := HashPassword("short"); !errors.Is(err, ErrInvalid) {
		t.Fatalf("short password: %v", err)
	}
	store := newMemoryStore()
	service := New(store)
	user, err := service.Register(context.Background(), " Demo@Example.com ", "研究员", "correct horse battery staple")
	if err != nil || user.Email != "demo@example.com" || user.Status != Pending || user.Role != Business {
		t.Fatalf("registered = %#v, %v", user, err)
	}
	if _, _, _, err := service.Login(context.Background(), user.Email, "correct horse battery staple"); !errors.Is(err, ErrPending) {
		t.Fatalf("pending login: %v", err)
	}
	if _, _, _, err := service.Login(context.Background(), user.Email, "incorrect password"); !errors.Is(err, ErrCredentials) {
		t.Fatalf("wrong password: %v", err)
	}
}

func TestRoutesEnforceRoleCSRFAndRevocation(t *testing.T) {
	store := newMemoryStore()
	service := New(store)
	ctx := context.Background()
	admin, _ := service.Register(ctx, "admin@example.com", "Admin", "correct horse battery staple")
	annotator, _ := service.Register(ctx, "annotator@example.com", "Annotator", "correct horse battery staple")
	business, _ := service.Register(ctx, "business@example.com", "Business", "correct horse battery staple")
	store.mu.Lock()
	for email, role := range map[string]Role{admin.Email: Admin, annotator.Email: Annotator, business.Email: Business} {
		a := store.accounts[email]
		a.Role, a.Status = role, Active
		store.accounts[email] = a
	}
	store.mu.Unlock()
	router := chi.NewRouter()
	service.RegisterRoutes(router)
	router.Get("/api/datasets", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	router.Get("/api/workers", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	router.Get("/api/workers/{id}", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	router.Get("/api/annotation/projects", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	router.Get("/api/review-items", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	router.Post("/api/training-runs", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(201) })
	router.Post("/api/annotation/publications/{id}/publish", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	server := httptest.NewServer(service.Protect(router))
	defer server.Close()

	request := func(method, path string, cookie *http.Cookie, csrf string) int {
		req, _ := http.NewRequest(method, server.URL+path, nil)
		if cookie != nil {
			req.AddCookie(cookie)
		}
		if csrf != "" {
			req.Header.Set("X-CSRF-Token", csrf)
		}
		response, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer response.Body.Close()
		return response.StatusCode
	}
	login := func(email string) (*http.Cookie, string) {
		payload, _ := json.Marshal(map[string]string{"email": email, "password": "correct horse battery staple"})
		response, err := http.Post(server.URL+"/api/auth/login", "application/json", bytes.NewReader(payload))
		if err != nil {
			t.Fatal(err)
		}
		defer response.Body.Close()
		if response.StatusCode != 200 {
			t.Fatalf("login %s = %d", email, response.StatusCode)
		}
		var result struct {
			CSRF string `json:"csrf_token"`
		}
		if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
			t.Fatal(err)
		}
		return response.Cookies()[0], result.CSRF
	}
	if got := request("GET", "/api/datasets", nil, ""); got != 401 {
		t.Fatalf("anonymous datasets = %d", got)
	}
	aCookie, aCSRF := login(annotator.Email)
	for _, path := range []string{"/api/workers", "/api/workers/test"} {
		if got := request("GET", path, nil, ""); got != 401 {
			t.Fatalf("anonymous workers = %d", got)
		}
		if got := request("GET", path, aCookie, ""); got != 403 {
			t.Fatalf("annotator workers = %d", got)
		}
	}
	if got := request("GET", "/api/review-items", aCookie, ""); got != 200 {
		t.Fatalf("annotator review = %d", got)
	}
	if got := request("GET", "/api/datasets", aCookie, ""); got != 200 {
		t.Fatalf("annotator dataset lookup = %d", got)
	}
	if got := request("POST", "/api/training-runs", aCookie, aCSRF); got != 403 {
		t.Fatalf("annotator training = %d", got)
	}
	if got := request("POST", "/api/annotation/publications/anything/publish", aCookie, aCSRF); got != 403 {
		t.Fatalf("annotator publish = %d", got)
	}
	bCookie, bCSRF := login(business.Email)
	if got := request("GET", "/api/workers", bCookie, ""); got != 200 {
		t.Fatalf("business workers = %d", got)
	}
	if got := request("GET", "/api/annotation/projects", bCookie, ""); got != 403 {
		t.Fatalf("business annotation = %d", got)
	}
	if got := request("POST", "/api/training-runs", bCookie, ""); got != 403 {
		t.Fatalf("missing csrf = %d", got)
	}
	if got := request("POST", "/api/training-runs", bCookie, bCSRF); got != 201 {
		t.Fatalf("business training = %d", got)
	}
	adminCookie, adminCSRF := login(admin.Email)
	if got := request("GET", "/api/auth/users", adminCookie, ""); got != 200 {
		t.Fatalf("admin users = %d", got)
	}
	if got := request("GET", "/api/auth/users", bCookie, ""); got != 403 {
		t.Fatalf("business users = %d", got)
	}
	change, _ := json.Marshal(map[string]string{"role": "annotator", "status": "active", "reason": "change team"})
	update, _ := http.NewRequest(http.MethodPatch, server.URL+"/api/auth/users/"+business.ID.String(), bytes.NewReader(change))
	update.AddCookie(adminCookie)
	update.Header.Set("Content-Type", "application/json")
	update.Header.Set("X-CSRF-Token", adminCSRF)
	updated, err := http.DefaultClient.Do(update)
	if err != nil {
		t.Fatal(err)
	}
	updated.Body.Close()
	if updated.StatusCode != 200 {
		t.Fatalf("admin role change = %d", updated.StatusCode)
	}
	if got := request("GET", "/api/datasets", bCookie, ""); got != 401 {
		t.Fatalf("old session after role change = %d", got)
	}
	if got := request("POST", "/api/auth/logout", adminCookie, adminCSRF); got != 204 {
		t.Fatalf("logout = %d", got)
	}
	if got := request("GET", "/api/auth/me", adminCookie, ""); got != 401 {
		t.Fatalf("logged-out session = %d", got)
	}
}
