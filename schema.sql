CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, student_id TEXT NOT NULL DEFAULT '',
 role TEXT NOT NULL DEFAULT 'student' CHECK(role IN ('student','admin')),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended')),
 can_request INTEGER NOT NULL DEFAULT 1, max_loans INTEGER NOT NULL DEFAULT 3 CHECK(max_loans>=0)
);
CREATE TABLE IF NOT EXISTS books (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, author TEXT NOT NULL,
 category TEXT NOT NULL DEFAULT 'General', isbn TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
 read_url TEXT NOT NULL DEFAULT '', total_copies INTEGER NOT NULL DEFAULT 1 CHECK(total_copies>=0),
 available_copies INTEGER NOT NULL DEFAULT 1 CHECK(available_copies>=0 AND available_copies<=total_copies),
 loan_days INTEGER NOT NULL DEFAULT 14 CHECK(loan_days>0),
 allow_softcopy INTEGER NOT NULL DEFAULT 1, allow_hardcopy INTEGER NOT NULL DEFAULT 1,
 restricted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS requests (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), book_id INTEGER NOT NULL REFERENCES books(id),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','issued','returned','rejected','cancelled')),
 note TEXT NOT NULL DEFAULT '', admin_note TEXT NOT NULL DEFAULT '',
 requested_at TEXT NOT NULL DEFAULT (datetime('now')), due_date TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    destination TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0 CHECK(is_read IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS notifications_user_unread
ON notifications(user_id, is_read);

CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    course TEXT NOT NULL,
    material_type TEXT NOT NULL CHECK(material_type IN ('Slides', 'Lecture notes', 'Teaching material', 'Other')),
    description TEXT NOT NULL DEFAULT '',
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    file_size INTEGER NOT NULL CHECK(file_size > 0),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
