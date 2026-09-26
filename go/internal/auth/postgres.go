package auth

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

type PostgresStore struct{ Pool *pgxpool.Pool }

func (store *PostgresStore) CreateUser(ctx context.Context, user User, passwordHash string) error {
	_, err := store.Pool.Exec(ctx, `INSERT INTO app_users (id,email,display_name,password_hash,role,status,created_at,updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`, user.ID, user.Email, user.DisplayName, passwordHash, user.Role, user.Status, user.CreatedAt, user.UpdatedAt)
	var pgerr *pgconn.PgError
	if errors.As(err, &pgerr) && pgerr.Code == "23505" {
		return ErrDuplicate
	}
	return err
}

func (store *PostgresStore) FindByEmail(ctx context.Context, email string) (Account, error) {
	var a Account
	err := store.Pool.QueryRow(ctx, `SELECT id,email,display_name,role,status,created_at,updated_at,password_hash,failed_logins,locked_until FROM app_users WHERE email=$1`, email).Scan(&a.ID, &a.Email, &a.DisplayName, &a.Role, &a.Status, &a.CreatedAt, &a.UpdatedAt, &a.PasswordHash, &a.FailedLogins, &a.LockedUntil)
	if errors.Is(err, pgx.ErrNoRows) {
		return Account{}, ErrNotFound
	}
	return a, err
}

func (store *PostgresStore) RecordFailedLogin(ctx context.Context, id uuid.UUID) error {
	_, err := store.Pool.Exec(ctx, `UPDATE app_users SET
      failed_logins=CASE WHEN locked_until<now() THEN 1 ELSE failed_logins+1 END,
      locked_until=CASE WHEN (CASE WHEN locked_until<now() THEN 1 ELSE failed_logins+1 END)>=5 THEN now()+interval '15 minutes' ELSE NULL END
      WHERE id=$1`, id)
	return err
}

func (store *PostgresStore) ClearFailedLogins(ctx context.Context, id uuid.UUID) error {
	_, err := store.Pool.Exec(ctx, `UPDATE app_users SET failed_logins=0,locked_until=NULL WHERE id=$1 AND (failed_logins<>0 OR locked_until IS NOT NULL)`, id)
	return err
}

func (store *PostgresStore) CreateSession(ctx context.Context, hash []byte, userID uuid.UUID, csrf string, expires time.Time) error {
	_, err := store.Pool.Exec(ctx, `INSERT INTO app_sessions (token_hash,user_id,csrf_token,expires_at) VALUES ($1,$2,$3,$4)`, hash, userID, csrf, expires)
	return err
}

func (store *PostgresStore) FindSession(ctx context.Context, hash []byte) (Session, error) {
	var session Session
	err := store.Pool.QueryRow(ctx, `SELECT u.id,u.email,u.display_name,u.role,u.status,u.created_at,u.updated_at,s.csrf_token
      FROM app_sessions s JOIN app_users u ON u.id=s.user_id
      WHERE s.token_hash=$1 AND s.expires_at>now() AND u.status='active'`, hash).Scan(
		&session.User.ID, &session.User.Email, &session.User.DisplayName, &session.User.Role, &session.User.Status,
		&session.User.CreatedAt, &session.User.UpdatedAt, &session.CSRFToken)
	if errors.Is(err, pgx.ErrNoRows) {
		return Session{}, ErrCredentials
	}
	return session, err
}

func (store *PostgresStore) DeleteSession(ctx context.Context, hash []byte) error {
	_, err := store.Pool.Exec(ctx, `DELETE FROM app_sessions WHERE token_hash=$1`, hash)
	return err
}

func (store *PostgresStore) ListUsers(ctx context.Context, limit, offset int, query string, status Status) ([]User, int, error) {
	filter := ` WHERE ($1='' OR email ILIKE '%'||$1||'%' OR display_name ILIKE '%'||$1||'%') AND ($2='' OR status=$2)`
	var total int
	if err := store.Pool.QueryRow(ctx, `SELECT count(*) FROM app_users`+filter, query, status).Scan(&total); err != nil {
		return nil, 0, err
	}
	rows, err := store.Pool.Query(ctx, `SELECT id,email,display_name,role,status,created_at,updated_at FROM app_users
      `+filter+` ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, created_at DESC LIMIT $3 OFFSET $4`, query, status, limit, offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	users := make([]User, 0)
	for rows.Next() {
		var user User
		if err := rows.Scan(&user.ID, &user.Email, &user.DisplayName, &user.Role, &user.Status, &user.CreatedAt, &user.UpdatedAt); err != nil {
			return nil, 0, err
		}
		users = append(users, user)
	}
	return users, total, rows.Err()
}

func (store *PostgresStore) UpdateUser(ctx context.Context, actorID, userID uuid.UUID, role Role, status Status, reason string) (User, error) {
	if !ValidRole(role) || !ValidStatus(status) {
		return User{}, ErrInvalid
	}
	if actorID == userID {
		return User{}, ErrInvalid
	}
	tx, err := store.Pool.Begin(ctx)
	if err != nil {
		return User{}, err
	}
	defer tx.Rollback(ctx)
	// Serialize role changes so concurrent administrators cannot remove the last active admin.
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(25092601)`); err != nil {
		return User{}, err
	}
	var before User
	err = tx.QueryRow(ctx, `SELECT id,email,display_name,role,status,created_at,updated_at FROM app_users WHERE id=$1 FOR UPDATE`, userID).Scan(
		&before.ID, &before.Email, &before.DisplayName, &before.Role, &before.Status, &before.CreatedAt, &before.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return User{}, ErrNotFound
	}
	if err != nil {
		return User{}, err
	}
	if before.Role == Admin && before.Status == Active && (role != Admin || status != Active) {
		var count int
		if err := tx.QueryRow(ctx, `SELECT count(*) FROM app_users WHERE role='admin' AND status='active'`).Scan(&count); err != nil {
			return User{}, err
		}
		if count <= 1 {
			return User{}, ErrLastAdmin
		}
	}
	var after User
	err = tx.QueryRow(ctx, `UPDATE app_users SET role=$2,status=$3,updated_at=now() WHERE id=$1
      RETURNING id,email,display_name,role,status,created_at,updated_at`, userID, role, status).Scan(
		&after.ID, &after.Email, &after.DisplayName, &after.Role, &after.Status, &after.CreatedAt, &after.UpdatedAt)
	if err != nil {
		return User{}, err
	}
	if _, err = tx.Exec(ctx, `DELETE FROM app_sessions WHERE user_id=$1`, userID); err != nil {
		return User{}, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO app_user_events (id,user_id,actor_id,event_type,from_role,to_role,from_status,to_status,reason)
      VALUES ($1,$2,$3,'access_updated',$4,$5,$6,$7,$8)`, uuid.New(), userID, actorID, before.Role, after.Role, before.Status, after.Status, reason); err != nil {
		return User{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return User{}, err
	}
	return after, nil
}

func (store *PostgresStore) BootstrapAdmin(ctx context.Context, email, name, passwordHash string) error {
	tx, err := store.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var id uuid.UUID
	err = tx.QueryRow(ctx, `INSERT INTO app_users (id,email,display_name,password_hash,role,status) VALUES ($1,$2,$3,$4,'admin','active')
      ON CONFLICT (email) DO UPDATE SET display_name=EXCLUDED.display_name,password_hash=EXCLUDED.password_hash,role='admin',status='active',failed_logins=0,locked_until=NULL,updated_at=now()
      RETURNING id`, uuid.New(), email, name, passwordHash).Scan(&id)
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `DELETE FROM app_sessions WHERE user_id=$1`, id); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO app_user_events (id,user_id,event_type,to_role,to_status,reason) VALUES ($1,$2,'bootstrap','admin','active','admin CLI')`, uuid.New(), id); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
