package auth

import (
	"crypto/sha256"
	"encoding/hex"
	"github.com/tianyuqic1/FGVCplateformPublic/agent/api/delegation"
	"net/http"
	"strings"
	"time"
)

// AgentAssertions are minted only after cookie authentication and CSRF checks.
func (s *Service) AgentAssertions(r *http.Request) (string, string, error) {
	u, ok := UserFromContext(r.Context())
	if !ok {
		return "", "", ErrCredentials
	}
	hash := sha256.Sum256([]byte(s.secret(r)))
	c := delegation.Claims{Actor: u.ID.String(), Role: string(u.Role), Session: hex.EncodeToString(hash[:]), Purpose: "interactive", Expires: time.Now().Add(2 * time.Minute).Unix()}
	identity, err := delegation.Sign(s.AgentKey, c)
	if err != nil {
		return "", "", err
	}
	c.Purpose = "tools"
	c.Expires = time.Now().Add(30 * time.Minute).Unix()
	grant, err := delegation.Sign(s.AgentKey, c)
	return identity, grant, err
}
func (s *Service) agentSession(r *http.Request) (Session, error) {
	if !strings.HasPrefix(r.URL.Path, "/api/agent-tools/") {
		return Session{}, ErrCredentials
	}
	c, err := delegation.Verify(s.AgentKey, r.Header.Get("X-Agent-Delegation"), time.Now())
	if err != nil {
		return Session{}, ErrCredentials
	}
	if strings.Contains(r.URL.Path, "/approvals/") && c.Purpose != "interactive" {
		return Session{}, ErrCredentials
	}
	hash, err := hex.DecodeString(c.Session)
	if err != nil {
		return Session{}, ErrCredentials
	}
	session, err := s.Store.FindSession(r.Context(), hash)
	if err != nil {
		return Session{}, err
	}
	if session.User.ID.String() != c.Actor || session.User.Status != Active {
		return Session{}, ErrCredentials
	}
	return session, nil // Current role is deliberately read from the platform on every call.
}
