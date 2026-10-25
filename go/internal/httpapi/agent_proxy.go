package httpapi

import (
	"github.com/go-chi/chi/v5"
	"github.com/tianyuqic1/FGVCplateformPublic/go/internal/auth"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
)

func registerAgentProxy(router chi.Router, authentication *auth.Service, origin string) {
	target, err := url.Parse(origin)
	available := err == nil && target.Host != "" && (target.Scheme == "http" || target.Scheme == "https") && target.User == nil && target.Path == "" && target.RawQuery == "" && target.Fragment == ""
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !available || authentication == nil {
			reviewResponse(w, 503, map[string]string{"detail": "Agent 服务尚未配置"})
			return
		}
		identity, grant, err := authentication.AgentAssertions(r)
		if err != nil {
			reviewResponse(w, 503, map[string]string{"detail": "Agent 身份签名不可用"})
			return
		}
		proxy := &httputil.ReverseProxy{FlushInterval: -1, Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetURL(target)
			pr.Out.URL.Path = "/v1/" + strings.TrimPrefix(pr.In.URL.Path, "/api/agent/")
			pr.Out.URL.RawPath = ""
			pr.Out.Header.Del("Cookie")
			pr.Out.Header.Del("Authorization")
			pr.Out.Header.Del("X-CSRF-Token")
			pr.Out.Header.Del("X-Agent-Delegation")
			pr.Out.Header.Set("X-Agent-Identity", identity)
			pr.Out.Header.Set("X-Agent-Grant", grant)
		}, ErrorHandler: func(w http.ResponseWriter, _ *http.Request, _ error) {
			reviewResponse(w, 503, map[string]string{"detail": "Agent 暂不可用，请稍后重试"})
		}}
		proxy.ServeHTTP(w, r)
	})
	router.Handle("/api/agent/*", handler)
}
