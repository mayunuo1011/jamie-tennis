ALTER TABLE credit_ledger ADD COLUMN active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1));
ALTER TABLE credit_ledger ADD COLUMN reverses_id INTEGER REFERENCES credit_ledger(id);
UPDATE credit_ledger SET reverses_id=(SELECT c.id FROM credit_ledger c WHERE c.lesson_id=credit_ledger.lesson_id AND c.action IN ('lesson','absence')) WHERE action='return';
UPDATE credit_ledger SET active=0 WHERE action IN ('lesson','absence') AND id IN (SELECT reverses_id FROM credit_ledger WHERE action='return');
DROP INDEX one_lesson_charge;
DROP INDEX one_lesson_return;
CREATE UNIQUE INDEX one_lesson_charge ON credit_ledger(lesson_id) WHERE action IN ('lesson','absence') AND active=1;
CREATE UNIQUE INDEX one_return_per_charge ON credit_ledger(reverses_id) WHERE action='return';
CREATE TABLE group_reminders(
 id INTEGER PRIMARY KEY,coach_id INTEGER NOT NULL REFERENCES coaches(id),
 name TEXT NOT NULL,starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,location TEXT NOT NULL,
 archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE group_members(
 group_id INTEGER NOT NULL REFERENCES group_reminders(id),student_id INTEGER NOT NULL REFERENCES students(id),
 PRIMARY KEY(group_id,student_id)
);
