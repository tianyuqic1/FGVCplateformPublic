package observabilityconsole

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"net/http"
	"strings"
	"time"
)

const sessionCookieName = "finevision_observability_session"

func (service *Service) authMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if !service.authEnabled || request.URL.Path == "/health" || request.URL.Path == "/metrics" {
			next.ServeHTTP(writer, request)
			return
		}
		if request.URL.Path == "/" && request.URL.Query().Has("token") {
			if service.matchesToken(request.URL.Query().Get("token")) {
				http.SetCookie(writer, &http.Cookie{Name: sessionCookieName, Value: hex.EncodeToString(service.accessTokenHash[:]), Path: "/", HttpOnly: true, SameSite: http.SameSiteStrictMode, MaxAge: int((8 * time.Hour).Seconds())})
				http.Redirect(writer, request, "/", http.StatusSeeOther)
				return
			}
			service.writeLogin(writer, http.StatusUnauthorized, "令牌无效，请重新输入。")
			return
		}
		if service.authorized(request) {
			next.ServeHTTP(writer, request)
			return
		}
		if strings.HasPrefix(request.URL.Path, "/api/") {
			writeError(writer, http.StatusUnauthorized, "OBSERVABILITY_AUTH_REQUIRED", "需要观测控制台访问令牌")
			return
		}
		service.writeLogin(writer, http.StatusUnauthorized, "此控制台已启用访问保护。")
	})
}

func (service *Service) authorized(request *http.Request) bool {
	if authorization := strings.TrimSpace(request.Header.Get("Authorization")); strings.HasPrefix(strings.ToLower(authorization), "bearer ") {
		return service.matchesToken(strings.TrimSpace(authorization[7:]))
	}
	cookie, err := request.Cookie(sessionCookieName)
	if err != nil {
		return false
	}
	expected := hex.EncodeToString(service.accessTokenHash[:])
	return subtle.ConstantTimeCompare([]byte(cookie.Value), []byte(expected)) == 1
}

func (service *Service) matchesToken(candidate string) bool {
	digest := sha256.Sum256([]byte(candidate))
	return subtle.ConstantTimeCompare(digest[:], service.accessTokenHash[:]) == 1
}

func (service *Service) writeLogin(writer http.ResponseWriter, status int, message string) {
	writer.Header().Set("Content-Type", "text/html; charset=utf-8")
	writer.Header().Set("Cache-Control", "no-store")
	writer.WriteHeader(status)
	_, _ = writer.Write([]byte(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>FineVision 观测控制台授权</title><style>*{box-sizing:border-box}body{margin:0;display:grid;place-items:center;min-height:100vh;color:#122b49;background:radial-gradient(circle at 15% 10%,#dff4f4,transparent 34%),radial-gradient(circle at 90% 15%,#ece9f9,transparent 35%),#f4f7fa;font-family:Inter,"Microsoft YaHei",sans-serif}.card{width:min(430px,calc(100% - 32px));padding:32px;border:1px solid #d8e3ec;border-radius:16px;background:rgba(255,255,255,.9);box-shadow:0 25px 70px rgba(30,54,83,.12)}.mark{display:grid;place-items:center;width:42px;height:42px;border-radius:11px;color:white;background:linear-gradient(135deg,#147f76,#315fbd);font-weight:900}h1{margin:20px 0 8px;font-size:25px}p{margin:0 0 20px;color:#6a7c91;line-height:1.6}label{display:grid;gap:7px;color:#5f7288;font-size:12px;font-weight:800}input{width:100%;height:44px;padding:0 12px;border:1px solid #cfdae5;border-radius:9px;outline:none}input:focus{border-color:#5c82ca;box-shadow:0 0 0 3px rgba(49,95,189,.12)}button{width:100%;height:44px;margin-top:12px;border:0;border-radius:9px;color:white;background:linear-gradient(135deg,#147f76,#315fbd);font-weight:800;cursor:pointer}small{display:block;margin-top:18px;color:#8795a5;line-height:1.5}</style></head><body><main class="card"><div class="mark">FV</div><h1>日志与观测控制台</h1><p>` + htmlEscape(message) + `</p><form method="get" action="/"><label>访问令牌<input type="password" name="token" autocomplete="current-password" required autofocus></label><button type="submit">进入控制台</button></form><small>验证成功后 URL 中的令牌会立即移除，并写入仅限当前站点的 HttpOnly 会话 Cookie。</small></main></body></html>`))
}

func htmlEscape(value string) string {
	replacer := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&#39;")
	return replacer.Replace(value)
}
