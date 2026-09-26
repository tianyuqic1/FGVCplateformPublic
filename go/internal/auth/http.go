package auth

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type contextKey struct{}
type sessionContextKey struct{}

func UserFromContext(ctx context.Context) (User, bool) {
	user, ok := ctx.Value(contextKey{}).(User)
	return user, ok
}

func Actor(ctx context.Context, fallback string) string {
	if user, ok := UserFromContext(ctx); ok {
		return user.ID.String()
	}
	return fallback
}

func (s *Service) cookieName() string {
	if s.CookieSecure {
		return "__Host-fv_session"
	}
	return "fv_session"
}

func (s *Service) setCookie(w http.ResponseWriter, secret string, maxAge int) {
	http.SetCookie(w, &http.Cookie{Name: s.cookieName(), Value: secret, Path: "/", HttpOnly: true,
		Secure: s.CookieSecure, SameSite: http.SameSiteLaxMode, MaxAge: maxAge})
}

func (s *Service) secret(r *http.Request) string {
	cookie, err := r.Cookie(s.cookieName())
	if err != nil {
		return ""
	}
	return cookie.Value
}

func respond(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	if value != nil {
		_ = json.NewEncoder(w).Encode(value)
	}
}

func decodeJSON(w http.ResponseWriter, r *http.Request, value any) bool {
	mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" {
		respond(w, 415, map[string]string{"detail": "需要 application/json 请求体"})
		return false
	}
	r.Body = http.MaxBytesReader(w, r.Body, 4096)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if decoder.Decode(value) != nil || decoder.Decode(new(any)) != io.EOF {
		respond(w, 400, map[string]string{"detail": "请求内容无效"})
		return false
	}
	return true
}

func (s *Service) RegisterRoutes(router chi.Router) {
	router.Post("/api/auth/register", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Email       string `json:"email"`
			DisplayName string `json:"display_name"`
			Password    string `json:"password"`
		}
		if !decodeJSON(w, r, &input) {
			return
		}
		user, err := s.Register(r.Context(), input.Email, input.DisplayName, input.Password)
		if errors.Is(err, ErrDuplicate) {
			respond(w, 409, map[string]string{"detail": "邮箱已注册"})
			return
		}
		if errors.Is(err, ErrInvalid) {
			respond(w, 422, map[string]string{"detail": "请填写有效邮箱、姓名和至少 12 位密码"})
			return
		}
		if err != nil {
			respond(w, 503, map[string]string{"detail": "注册暂不可用"})
			return
		}
		respond(w, 201, map[string]any{"user": user, "message": "注册成功，等待管理员审核"})
	})
	router.Post("/api/auth/login", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Email    string `json:"email"`
			Password string `json:"password"`
		}
		if !decodeJSON(w, r, &input) {
			return
		}
		user, secret, csrf, err := s.Login(r.Context(), input.Email, input.Password)
		switch {
		case errors.Is(err, ErrPending):
			respond(w, 403, map[string]string{"detail": "账号等待管理员审核"})
			return
		case errors.Is(err, ErrDisabled):
			respond(w, 403, map[string]string{"detail": "账号已停用"})
			return
		case errors.Is(err, ErrLocked):
			respond(w, 429, map[string]string{"detail": "登录失败次数过多，请稍后再试"})
			return
		case errors.Is(err, ErrCredentials):
			respond(w, 401, map[string]string{"detail": "邮箱或密码错误"})
			return
		case err != nil:
			respond(w, 503, map[string]string{"detail": "登录暂不可用"})
			return
		}
		s.setCookie(w, secret, 12*60*60)
		respond(w, 200, map[string]any{"user": user, "csrf_token": csrf})
	})
	router.Get("/api/auth/me", func(w http.ResponseWriter, r *http.Request) {
		session, ok := r.Context().Value(sessionContextKey{}).(Session)
		if !ok {
			respond(w, 401, map[string]string{"detail": "请先登录"})
			return
		}
		respond(w, 200, map[string]any{"user": session.User, "csrf_token": session.CSRFToken})
	})
	router.Post("/api/auth/logout", func(w http.ResponseWriter, r *http.Request) {
		if err := s.Logout(r.Context(), s.secret(r)); err != nil {
			respond(w, 503, map[string]string{"detail": "退出失败"})
			return
		}
		s.setCookie(w, "", -1)
		respond(w, 204, nil)
	})
	router.Get("/api/auth/users", func(w http.ResponseWriter, r *http.Request) {
		page, err := strconv.Atoi(r.URL.Query().Get("page"))
		if r.URL.Query().Get("page") == "" {
			page = 1
			err = nil
		}
		if err != nil || page < 1 || page > 100000 {
			respond(w, 422, map[string]string{"detail": "页码无效"})
			return
		}
		query := strings.TrimSpace(r.URL.Query().Get("q"))
		status := Status(r.URL.Query().Get("status"))
		if len(query) > 100 || (status != "" && !ValidStatus(status)) {
			respond(w, 422, map[string]string{"detail": "筛选条件无效"})
			return
		}
		users, total, err := s.Store.ListUsers(r.Context(), 20, (page-1)*20, query, status)
		if err != nil {
			respond(w, 503, map[string]string{"detail": "用户列表暂不可用"})
			return
		}
		respond(w, 200, map[string]any{"items": users, "total": total, "page": page, "page_size": 20})
	})
	router.Patch("/api/auth/users/{user_id}", func(w http.ResponseWriter, r *http.Request) {
		id, err := uuid.Parse(chi.URLParam(r, "user_id"))
		if err != nil {
			respond(w, 422, map[string]string{"detail": "用户 ID 无效"})
			return
		}
		var input struct {
			Role   Role   `json:"role"`
			Status Status `json:"status"`
			Reason string `json:"reason"`
		}
		if !decodeJSON(w, r, &input) {
			return
		}
		if !ValidRole(input.Role) || !ValidStatus(input.Status) || strings.TrimSpace(input.Reason) == "" || len(input.Reason) > 500 {
			respond(w, 422, map[string]string{"detail": "角色、状态或变更原因无效"})
			return
		}
		actor, _ := UserFromContext(r.Context())
		user, err := s.Store.UpdateUser(r.Context(), actor.ID, id, input.Role, input.Status, strings.TrimSpace(input.Reason))
		switch {
		case errors.Is(err, ErrNotFound):
			respond(w, 404, map[string]string{"detail": "用户不存在"})
			return
		case errors.Is(err, ErrLastAdmin):
			respond(w, 409, map[string]string{"detail": "不能移除最后一位管理员"})
			return
		case errors.Is(err, ErrInvalid):
			respond(w, 422, map[string]string{"detail": "不能修改自己的角色或状态"})
			return
		case err != nil:
			respond(w, 503, map[string]string{"detail": "更新用户失败"})
			return
		}
		respond(w, 200, map[string]any{"user": user})
	})
}

func isPublic(path string) bool {
	return path == "/api/health" || path == "/api/auth/register" || path == "/api/auth/login"
}

func isServicePath(path string) bool {
	return path == "/api/internal/hardware/samples" || strings.HasPrefix(path, "/api/internal/deployments/") || strings.HasPrefix(path, "/api/annotation/internal/")
}

func allowed(role Role, method, path string) bool {
	if role == Admin {
		return true
	}
	if path == "/api/auth/me" || path == "/api/auth/logout" {
		return true
	}
	if strings.HasPrefix(path, "/api/auth/") || strings.HasPrefix(path, "/swagger") || strings.HasPrefix(path, "/openapi/") {
		return false
	}
	if role == Business {
		return !strings.HasPrefix(path, "/api/annotation/")
	}
	if role != Annotator {
		return false
	}
	if strings.HasPrefix(path, "/api/annotation/") {
		// Registration of a finished dataset version remains an administrator action.
		if method == http.MethodPost && strings.HasPrefix(path, "/api/annotation/publications/") &&
			(strings.HasSuffix(path, "/publish") || strings.HasSuffix(path, "/retry") || strings.HasSuffix(path, "/cancel")) {
			return false
		}
		return true
	}
	if strings.HasPrefix(path, "/api/review-items") || strings.HasPrefix(path, "/api/uploads/") {
		return true
	}
	if method == http.MethodGet && (path == "/api/datasets" || strings.HasPrefix(path, "/api/datasets/") ||
		strings.HasPrefix(path, "/api/dataset-versions/") || strings.HasPrefix(path, "/api/abstention-policies") ||
		strings.HasPrefix(path, "/api/feedback-items") || strings.HasPrefix(path, "/api/model-versions")) {
		return true
	}
	return false
}

func (s *Service) Protect(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		if isPublic(path) || isServicePath(path) || path == "/metrics" {
			next.ServeHTTP(w, r)
			return
		}
		if strings.HasPrefix(path, "/internal/") {
			http.NotFound(w, r)
			return
		}
		secret := s.secret(r)
		if secret == "" {
			respond(w, 401, map[string]string{"detail": "请先登录"})
			return
		}
		session, err := s.Session(r.Context(), secret)
		if err != nil {
			if !errors.Is(err, ErrCredentials) {
				respond(w, 503, map[string]string{"detail": "会话服务暂不可用"})
				return
			}
			respond(w, 401, map[string]string{"detail": "登录已过期"})
			return
		}
		if !allowed(session.User.Role, r.Method, path) {
			respond(w, 403, map[string]string{"detail": "当前账号无权访问"})
			return
		}
		if r.Method != http.MethodGet && r.Method != http.MethodHead && r.Method != http.MethodOptions {
			provided := r.Header.Get("X-CSRF-Token")
			if provided == "" || subtle.ConstantTimeCompare([]byte(provided), []byte(session.CSRFToken)) != 1 {
				respond(w, 403, map[string]string{"detail": "CSRF 校验失败，请刷新页面"})
				return
			}
		}
		ctx := context.WithValue(r.Context(), contextKey{}, session.User)
		ctx = context.WithValue(ctx, sessionContextKey{}, session)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}
