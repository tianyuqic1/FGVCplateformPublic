package auth

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"net/mail"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"golang.org/x/crypto/argon2"
)

type Role string
type Status string

const (
	Admin     Role   = "admin"
	Annotator Role   = "annotator"
	Business  Role   = "business"
	Pending   Status = "pending"
	Active    Status = "active"
	Disabled  Status = "disabled"
)

var (
	ErrInvalid     = errors.New("invalid account input")
	ErrDuplicate   = errors.New("email already registered")
	ErrCredentials = errors.New("invalid email or password")
	ErrPending     = errors.New("account pending approval")
	ErrDisabled    = errors.New("account disabled")
	ErrLocked      = errors.New("account temporarily locked")
	ErrNotFound    = errors.New("account not found")
	ErrLastAdmin   = errors.New("cannot remove the last active administrator")
)

type User struct {
	ID          uuid.UUID `json:"id"`
	Email       string    `json:"email"`
	DisplayName string    `json:"display_name"`
	Role        Role      `json:"role"`
	Status      Status    `json:"status"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

type Account struct {
	User
	PasswordHash string
	FailedLogins int
	LockedUntil  *time.Time
}

type Session struct {
	User      User
	CSRFToken string
}

type Store interface {
	CreateUser(context.Context, User, string) error
	FindByEmail(context.Context, string) (Account, error)
	RecordFailedLogin(context.Context, uuid.UUID) error
	ClearFailedLogins(context.Context, uuid.UUID) error
	CreateSession(context.Context, []byte, uuid.UUID, string, time.Time) error
	FindSession(context.Context, []byte) (Session, error)
	DeleteSession(context.Context, []byte) error
	ListUsers(context.Context, int, int, string, Status) ([]User, int, error)
	UpdateUser(context.Context, uuid.UUID, uuid.UUID, Role, Status, string) (User, error)
}

type Service struct {
	Store        Store
	Now          func() time.Time
	CookieSecure bool
}

func New(store Store) *Service { return &Service{Store: store, Now: time.Now} }

func ValidRole(role Role) bool { return role == Admin || role == Annotator || role == Business }
func ValidStatus(status Status) bool {
	return status == Pending || status == Active || status == Disabled
}

func NormalizeEmail(value string) (string, error) {
	email := strings.ToLower(strings.TrimSpace(value))
	parsed, err := mail.ParseAddress(email)
	if err != nil || parsed.Address != email || len(email) > 254 || len(email) < 3 {
		return "", ErrInvalid
	}
	return email, nil
}

func ValidatePassword(value string) error {
	if !utf8.ValidString(value) || len(value) < 12 || len(value) > 1024 {
		return ErrInvalid
	}
	return nil
}

func HashPassword(password string) (string, error) {
	if err := ValidatePassword(password); err != nil {
		return "", err
	}
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	key := argon2.IDKey([]byte(password), salt, 2, 19*1024, 1, 32)
	return fmt.Sprintf("$argon2id$v=19$m=19456,t=2,p=1$%s$%s", base64.RawStdEncoding.EncodeToString(salt), base64.RawStdEncoding.EncodeToString(key)), nil
}

func VerifyPassword(encoded, password string) bool {
	parts := strings.Split(encoded, "$")
	if len(parts) != 6 || parts[0] != "" || parts[1] != "argon2id" || parts[2] != "v=19" || parts[3] != "m=19456,t=2,p=1" {
		return false
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[4])
	if err != nil || len(salt) != 16 {
		return false
	}
	want, err := base64.RawStdEncoding.DecodeString(parts[5])
	if err != nil || len(want) != 32 {
		return false
	}
	got := argon2.IDKey([]byte(password), salt, 2, 19*1024, 1, 32)
	return subtle.ConstantTimeCompare(got, want) == 1
}

func (s *Service) Register(ctx context.Context, email, name, password string) (User, error) {
	normalized, err := NormalizeEmail(email)
	name = strings.TrimSpace(name)
	if err != nil || name == "" || utf8.RuneCountInString(name) > 80 || !utf8.ValidString(name) {
		return User{}, ErrInvalid
	}
	hash, err := HashPassword(password)
	if err != nil {
		return User{}, err
	}
	now := s.Now().UTC()
	user := User{ID: uuid.New(), Email: normalized, DisplayName: name, Role: Business, Status: Pending, CreatedAt: now, UpdatedAt: now}
	if err := s.Store.CreateUser(ctx, user, hash); err != nil {
		return User{}, err
	}
	return user, nil
}

func (s *Service) Login(ctx context.Context, email, password string) (User, string, string, error) {
	normalized, err := NormalizeEmail(email)
	if err != nil {
		return User{}, "", "", ErrCredentials
	}
	account, err := s.Store.FindByEmail(ctx, normalized)
	if errors.Is(err, ErrNotFound) {
		return User{}, "", "", ErrCredentials
	}
	if err != nil {
		return User{}, "", "", err
	}
	if account.LockedUntil != nil && account.LockedUntil.After(s.Now()) {
		return User{}, "", "", ErrLocked
	}
	if !VerifyPassword(account.PasswordHash, password) {
		_ = s.Store.RecordFailedLogin(ctx, account.ID)
		return User{}, "", "", ErrCredentials
	}
	if err := s.Store.ClearFailedLogins(ctx, account.ID); err != nil {
		return User{}, "", "", err
	}
	if account.Status == Pending {
		return User{}, "", "", ErrPending
	}
	if account.Status != Active {
		return User{}, "", "", ErrDisabled
	}
	secret, err := randomToken()
	if err != nil {
		return User{}, "", "", err
	}
	csrf, err := randomToken()
	if err != nil {
		return User{}, "", "", err
	}
	digest := sha256.Sum256([]byte(secret))
	if err := s.Store.CreateSession(ctx, digest[:], account.ID, csrf, s.Now().Add(12*time.Hour)); err != nil {
		return User{}, "", "", err
	}
	return account.User, secret, csrf, nil
}

func (s *Service) Session(ctx context.Context, secret string) (Session, error) {
	if len(secret) != 43 {
		return Session{}, ErrCredentials
	}
	digest := sha256.Sum256([]byte(secret))
	return s.Store.FindSession(ctx, digest[:])
}

func (s *Service) Logout(ctx context.Context, secret string) error {
	if secret == "" {
		return nil
	}
	digest := sha256.Sum256([]byte(secret))
	return s.Store.DeleteSession(ctx, digest[:])
}

func randomToken() (string, error) {
	value := make([]byte, 32)
	if _, err := rand.Read(value); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(value), nil
}
