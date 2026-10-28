ALTER TABLE packages ADD COLUMN is_debt INTEGER NOT NULL DEFAULT 0 CHECK(is_debt IN (0,1));
CREATE TABLE student_accounts(
 student_id INTEGER PRIMARY KEY REFERENCES students(id),
 password_hash TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 must_change INTEGER NOT NULL DEFAULT 1 CHECK(must_change IN (0,1))
);
CREATE TABLE student_sessions(
 token_hash TEXT PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES student_accounts(student_id),
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, last_seen INTEGER NOT NULL
);
CREATE TABLE student_login_limits(
 username TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start INTEGER NOT NULL,
 blocked_until INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE booking_slots(
 id INTEGER PRIMARY KEY, coach_id INTEGER NOT NULL REFERENCES coaches(id),
 kind TEXT NOT NULL CHECK(kind IN ('私教','陪打')),
 starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, location TEXT NOT NULL,
 closed INTEGER NOT NULL DEFAULT 0 CHECK(closed IN (0,1)),
 revision INTEGER NOT NULL DEFAULT 0,
 CHECK(ends_at>starts_at)
);
ALTER TABLE lessons ADD COLUMN booking_slot_id INTEGER REFERENCES booking_slots(id);
CREATE INDEX lessons_slot ON lessons(booking_slot_id);
CREATE TABLE student_booking_requests(
 student_id INTEGER NOT NULL REFERENCES students(id), request_key TEXT NOT NULL,
 slot_id INTEGER NOT NULL REFERENCES booking_slots(id), lesson_id INTEGER NOT NULL REFERENCES lessons(id),
 PRIMARY KEY(student_id,request_key)
);
