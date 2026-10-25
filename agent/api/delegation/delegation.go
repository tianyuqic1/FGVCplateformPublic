// Package delegation defines bounded, signed platform identity assertions.
package delegation

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
	"time"
)

type Claims struct {
	Actor   string `json:"actor"`
	Role    string `json:"role"`
	Session string `json:"session"`
	Purpose string `json:"purpose"`
	Expires int64  `json:"expires"`
}

func Sign(key []byte, c Claims) (string, error) {
	if len(key) < 32 || c.Actor == "" || c.Session == "" {
		return "", errors.New("invalid delegation configuration")
	}
	b, e := json.Marshal(c)
	if e != nil {
		return "", e
	}
	payload := base64.RawURLEncoding.EncodeToString(b)
	h := hmac.New(sha256.New, key)
	h.Write([]byte(payload))
	return payload + "." + base64.RawURLEncoding.EncodeToString(h.Sum(nil)), nil
}
func Verify(key []byte, token string, now time.Time) (Claims, error) {
	var c Claims
	if len(key) < 32 || len(token) > 2048 {
		return c, errors.New("invalid delegation")
	}
	parts := strings.Split(token, ".")
	if len(parts) != 2 {
		return c, errors.New("invalid delegation")
	}
	sig, e := base64.RawURLEncoding.DecodeString(parts[1])
	h := hmac.New(sha256.New, key)
	h.Write([]byte(parts[0]))
	if e != nil || !hmac.Equal(sig, h.Sum(nil)) {
		return c, errors.New("invalid delegation")
	}
	b, e := base64.RawURLEncoding.DecodeString(parts[0])
	if e != nil || json.Unmarshal(b, &c) != nil || c.Actor == "" || len(c.Session) != 64 || c.Expires <= now.Unix() || c.Expires > now.Add(12*time.Hour).Unix() || (c.Purpose != "interactive" && c.Purpose != "tools") {
		return Claims{}, errors.New("expired or invalid delegation")
	}
	return c, nil
}
