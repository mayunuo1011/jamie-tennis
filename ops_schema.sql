-- Phase 1 additive migration. Execute with ops_store.migrate(), not manually.
CREATE TABLE ops_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
CREATE TABLE coaches(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE accounts(
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
 display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('coach','operator','system_admin')),
 coach_id INTEGER UNIQUE REFERENCES coaches(id),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 created_at INTEGER NOT NULL,
 CHECK(role='system_admin' OR coach_id IS NOT NULL)
);
CREATE TABLE auth_sessions(
 token_hash TEXT PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, last_seen INTEGER NOT NULL
);
CREATE TABLE login_limits(
 username TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start INTEGER NOT NULL,
 blocked_until INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE student_coaches(
 student_id INTEGER NOT NULL REFERENCES students(id), coach_id INTEGER NOT NULL REFERENCES coaches(id),
 PRIMARY KEY(student_id,coach_id)
);
CREATE TABLE ops_audit(
 id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES accounts(id),
 action TEXT NOT NULL, entity TEXT NOT NULL, entity_id INTEGER,
 details TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX sessions_account ON auth_sessions(account_id);
CREATE INDEX ops_audit_time ON ops_audit(created_at);
