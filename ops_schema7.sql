ALTER TABLE student_accounts ADD COLUMN login_name TEXT COLLATE NOCASE;
CREATE UNIQUE INDEX student_login_name ON student_accounts(login_name COLLATE NOCASE) WHERE login_name IS NOT NULL;
UPDATE student_accounts SET password_hash='disabled-passwordless',must_change=0;
DELETE FROM student_sessions;
ALTER TABLE booking_slots ADD COLUMN published INTEGER NOT NULL DEFAULT 0 CHECK(published IN (0,1));
UPDATE booking_slots SET published=1;
ALTER TABLE lessons ADD COLUMN attendance_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(attendance_confirmed IN (0,1));
UPDATE lessons SET attendance_confirmed=1 WHERE checkin_done=1 OR status='已完成';
