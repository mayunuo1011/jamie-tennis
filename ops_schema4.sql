CREATE TABLE credit_ledger(
 id INTEGER PRIMARY KEY,
 student_id INTEGER NOT NULL REFERENCES students(id),
 coach_id INTEGER NOT NULL REFERENCES coaches(id),
 kind TEXT NOT NULL CHECK(kind IN ('私教','陪打')),
 package_id INTEGER NOT NULL REFERENCES packages(id),
 lesson_id INTEGER REFERENCES lessons(id),
 action TEXT NOT NULL CHECK(action IN ('opening','purchase','void_purchase','lesson','absence','return')),
 delta INTEGER NOT NULL,
 actor_id INTEGER REFERENCES accounts(id),
 reason TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 CHECK((action IN ('lesson','absence') AND delta=-1 AND lesson_id IS NOT NULL)
 OR (action='return' AND delta=1 AND lesson_id IS NOT NULL)
 OR (action='purchase' AND delta>0 AND lesson_id IS NULL)
 OR (action='opening' AND delta>=0 AND lesson_id IS NULL)
 OR (action='void_purchase' AND delta<0 AND lesson_id IS NULL))
);
CREATE UNIQUE INDEX one_lesson_charge ON credit_ledger(lesson_id) WHERE action IN ('lesson','absence');
CREATE UNIQUE INDEX one_lesson_return ON credit_ledger(lesson_id) WHERE action='return';
CREATE UNIQUE INDEX one_package_origin ON credit_ledger(package_id) WHERE action IN ('opening','purchase');
CREATE UNIQUE INDEX one_package_void ON credit_ledger(package_id) WHERE action='void_purchase';
CREATE INDEX credit_scope ON credit_ledger(student_id,coach_id,kind,id);
CREATE TABLE credit_requests(
 account_id INTEGER NOT NULL REFERENCES accounts(id),
 request_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 package_id INTEGER NOT NULL REFERENCES packages(id),
 PRIMARY KEY(account_id,request_key)
);
