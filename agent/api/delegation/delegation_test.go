package delegation

import (
	"strings"
	"testing"
	"time"
)

func TestAssertionIntegrityPurposeAndExpiry(t *testing.T) {
	key := []byte(strings.Repeat("k", 32))
	now := time.Now()
	c := Claims{Actor: "user", Role: "business", Session: strings.Repeat("a", 64), Purpose: "tools", Expires: now.Add(time.Minute).Unix()}
	token, e := Sign(key, c)
	if e != nil {
		t.Fatal(e)
	}
	out, e := Verify(key, token, now)
	if e != nil || out != c {
		t.Fatal(e)
	}
	if _, e = Verify(key, token+"x", now); e == nil {
		t.Fatal("tampering accepted")
	}
	if _, e = Verify(key, token, now.Add(2*time.Minute)); e == nil {
		t.Fatal("expired token accepted")
	}
	c.Purpose = "admin"
	token, _ = Sign(key, c)
	if _, e = Verify(key, token, now); e == nil {
		t.Fatal("invalid purpose accepted")
	}
}
