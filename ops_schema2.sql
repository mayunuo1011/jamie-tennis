-- Existing shared notes are retained in daily_notes without guessing an owner.
ALTER TABLE lessons ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE lessons ADD COLUMN archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1));
ALTER TABLE students ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
CREATE TABLE account_notes(
 account_id INTEGER NOT NULL REFERENCES accounts(id),day TEXT NOT NULL,body TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(account_id,day)
);
CREATE TABLE student_preferences(
 account_id INTEGER NOT NULL REFERENCES accounts(id),student_id INTEGER NOT NULL REFERENCES students(id),
 starred INTEGER NOT NULL DEFAULT 0 CHECK(starred IN (0,1)),
 PRIMARY KEY(account_id,student_id)
);
CREATE TABLE coach_student_profiles(
 student_id INTEGER NOT NULL,coach_id INTEGER NOT NULL,
 level TEXT NOT NULL DEFAULT '',pain_points TEXT NOT NULL DEFAULT '',
 goals TEXT NOT NULL DEFAULT '',remarks TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(student_id,coach_id),
 FOREIGN KEY(student_id,coach_id) REFERENCES student_coaches(student_id,coach_id)
);
CREATE TABLE ops_requests(
 account_id INTEGER NOT NULL REFERENCES accounts(id),request_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL,lesson_id INTEGER NOT NULL REFERENCES lessons(id),
 PRIMARY KEY(account_id,request_key)
);
