CREATE TABLE feedback_submissions(
 id INTEGER PRIMARY KEY,
 lesson_id INTEGER NOT NULL REFERENCES lessons(id),
 coach_id INTEGER NOT NULL REFERENCES coaches(id),
 feedback_revision INTEGER NOT NULL,
 note TEXT NOT NULL,next_goal TEXT NOT NULL,
 submitted_by INTEGER REFERENCES accounts(id),submitted_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','approved','returned','withdrawn')),
 is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
 reviewed_by INTEGER REFERENCES accounts(id),reviewed_at TEXT,review_comment TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL DEFAULT 'submission' CHECK(source IN ('submission','migration')),
 UNIQUE(lesson_id,feedback_revision)
);
CREATE UNIQUE INDEX one_current_submission ON feedback_submissions(lesson_id) WHERE is_current=1;
CREATE INDEX feedback_inbox ON feedback_submissions(is_current,state,submitted_at);
CREATE TABLE feedback_reads(
 submission_id INTEGER NOT NULL REFERENCES feedback_submissions(id),
 account_id INTEGER NOT NULL REFERENCES accounts(id),opened_at TEXT NOT NULL,
 PRIMARY KEY(submission_id,account_id)
);
