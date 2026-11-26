import sqlite3
from contextlib import contextmanager
import tempfile
import unittest
from pathlib import Path
from app import create_app
from werkzeug.security import generate_password_hash


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tmp.name) / 'test.sqlite')
        self.app = create_app({'TESTING': True, 'DATABASE': self.database, 'SECRET_KEY': 'testing-only'})
        self.client = self.app.test_client()
        with self.connection() as db:
            for name, role in [('Student', 'student'), ('Librarian', 'admin'), ('Other', 'student')]:
                db.execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)', (name, name.lower()+'@example.com', generate_password_hash('password123'), role))
            db.execute("INSERT INTO books(title,author,total_copies,available_copies) VALUES('Test Book','Test Author',1,1)")
            db.execute("INSERT INTO books(title,author,restricted) VALUES('Restricted Book','Author',1)")

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id
            session['csrf'] = 'test-token'

    def post(self, path, **data):
        with self.client.session_transaction() as session:
            token = session.get('csrf', 'test-token')
            session['csrf'] = token
        return self.client.post(path, data={'csrf': token, **data}, follow_redirects=True)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database)
        try:
            with db:
                yield db
        finally:
            db.close()

    def scalar(self, sql):
        with self.connection() as db:
            return db.execute(sql).fetchone()[0]

    def test_public_and_authenticated_pages(self):
        for path in ['/', '/about', '/auth', '/auth?mode=signup', '/static/style.css']:
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200, path)
        self.assertEqual(self.client.get('/library').status_code, 302)
        self.login(1)
        for path in ['/library', '/my-books', '/books/1']:
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200, path)
        self.assertEqual(self.client.get('/admin').status_code, 403)
        self.assertEqual(self.client.get('/books/2').status_code, 404)
        self.assertNotIn(b'Restricted Book', self.client.get('/library').data)
        self.login(2)
        for path in ['/admin', '/admin?tab=requests', '/admin?tab=users', '/admin?tab=books', '/admin/books/new', '/admin/books/1/edit']:
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200, path)

    def test_signup_signin_and_csrf(self):
        self.assertEqual(self.client.post('/auth', data={}).status_code, 400)
        response = self.post('/auth?mode=signup', name='New Member', email='new@example.com', password='password123', role='admin')
        self.assertIn(b'Catalogue', response.data)
        self.assertEqual(self.scalar("SELECT role FROM users WHERE email='new@example.com'"), 'student')
        self.assertNotEqual(self.scalar("SELECT password_hash FROM users WHERE email='new@example.com'"), 'password123')
        self.post('/logout')
        self.assertEqual(self.client.get('/my-books').status_code, 302)
        self.assertIn(b'incorrect', self.post('/auth', email='new@example.com', password='wrong').data)
        self.assertIn(b'Catalogue', self.post('/auth', email='new@example.com', password='password123').data)

    def test_full_loan_lifecycle_and_duplicate_request(self):
        self.login(1)
        self.post('/books/1/request')
        self.post('/books/1/request')
        self.assertEqual(self.scalar('SELECT count(*) FROM requests'), 1)
        self.assertEqual(self.post('/admin/requests/1', status='approved').status_code, 403)
        self.login(2)
        self.post('/admin/requests/1', status='approved')
        self.assertEqual(self.scalar('SELECT available_copies FROM books WHERE id=1'), 0)
        self.post('/admin/requests/1', status='issued')
        self.assertEqual(self.scalar('SELECT available_copies FROM books WHERE id=1'), 0)
        self.assertIsNotNone(self.scalar('SELECT due_date FROM requests WHERE id=1'))
        self.post('/admin/requests/1', status='returned')
        self.assertEqual(self.scalar('SELECT available_copies FROM books WHERE id=1'), 1)
        self.assertEqual(self.post('/admin/requests/1', status='returned').status_code, 400)
        self.assertEqual(self.scalar('SELECT available_copies FROM books WHERE id=1'), 1)

    def test_waitlist_and_cancellation_ownership(self):
        self.login(1)
        self.post('/books/1/request')
        self.login(2)
        self.post('/admin/requests/1', status='approved')
        self.login(3)
        self.assertEqual(self.post('/requests/1/cancel').status_code, 404)
        self.post('/books/1/request')
        self.login(2)
        self.post('/admin/requests/2', status='approved')
        self.assertEqual(self.scalar('SELECT status FROM requests WHERE id=2'), 'pending')
        self.login(1)
        self.post('/requests/1/cancel')
        self.assertEqual(self.scalar('SELECT available_copies FROM books WHERE id=1'), 1)
        self.login(2)
        self.post('/admin/requests/2', status='approved')
        self.assertEqual(self.scalar('SELECT status FROM requests WHERE id=2'), 'approved')

    def test_member_limits_and_restricted_book(self):
        self.login(1)
        self.assertEqual(self.post('/books/2/request').status_code, 404)
        with self.connection() as db:
            db.execute('UPDATE users SET max_loans=0 WHERE id=1')
        self.post('/books/1/request')
        self.assertEqual(self.scalar('SELECT count(*) FROM requests'), 0)
        with self.connection() as db:
            db.execute("UPDATE users SET max_loans=3,status='suspended' WHERE id=1")
        self.post('/books/1/request')
        self.assertEqual(self.scalar('SELECT count(*) FROM requests'), 0)

    def test_admin_book_validation_and_history(self):
        self.login(2)
        data = dict(title='New book', author='Someone', category='ICT', total_copies='2', loan_days='14', allow_hardcopy='on')
        self.post('/admin/books/new', **data, read_url='javascript:alert(1)')
        self.assertEqual(self.scalar('SELECT count(*) FROM books'), 2)
        self.post('/admin/books/new', **data)
        self.assertEqual(self.scalar('SELECT count(*) FROM books'), 3)
        self.login(1)
        self.post('/books/1/request')
        self.login(2)
        self.post('/admin/requests/1', status='approved')
        data['total_copies'] = '0'
        self.post('/admin/books/1/edit', **data)
        self.assertEqual(self.scalar('SELECT total_copies FROM books WHERE id=1'), 1)
        self.post('/admin/books/1/delete')
        self.assertEqual(self.scalar('SELECT count(*) FROM books WHERE id=1'), 1)
        self.assertEqual(self.post('/admin/users/2', role='student',status='active',max_loans='3').status_code, 400)
        self.post('/admin/users/1', role='student',status='suspended',max_loans='2')
        self.assertEqual(self.scalar('SELECT status FROM users WHERE id=1'), 'suspended')

    def test_admin_added_book_appears_in_student_catalogue(self):
        self.login(2)
        response = self.post(
            '/admin/books/new',
            title='New ICT Handbook',
            author='Library Team',
            category='ICT',
            total_copies='2',
            loan_days='14',
            allow_hardcopy='on',
        )
        self.assertIn(b'published to the student catalogue', response.data)

        student = self.app.test_client()
        with student.session_transaction() as session:
            session['user_id'] = 1
        response = student.get('/library')
        self.assertIn(b'New ICT Handbook', response.data)
        self.assertIn(b'Library Team', response.data)
        self.assertIn(b'2/2 copies', response.data)

    def test_notifications_for_request_lifecycle_and_ownership(self):
        self.login(1)
        self.assertIn(b"caught up", self.client.get('/notifications').data)
        self.post('/books/1/request')
        self.assertEqual(self.scalar('SELECT count(*) FROM notifications WHERE user_id=2'), 1)
        self.assertNotIn(b'Student requested', self.client.get('/notifications').data)
        self.assertEqual(self.post('/notifications/1/open').status_code, 404)
        self.assertEqual(self.scalar('SELECT is_read FROM notifications WHERE id=1'), 0)

        self.login(2)
        self.assertIn(b'1 unread', self.client.get('/library').data)
        self.post('/notifications/1/open')
        self.assertEqual(self.scalar('SELECT is_read FROM notifications WHERE id=1'), 1)
        self.post('/admin/requests/1', status='approved')
        self.post('/admin/requests/1', status='issued')
        self.post('/admin/requests/1', status='returned')

        self.login(1)
        response = self.client.get('/notifications')
        self.assertIn(b'Ready for pickup', response.data)
        self.assertIn(b'Due ', response.data)
        self.assertIn(b'Returned', response.data)
        self.assertEqual(self.client.post('/notifications/read-all').status_code, 400)
        self.post('/notifications/read-all')
        self.assertEqual(self.scalar('SELECT count(*) FROM notifications WHERE user_id=1 AND is_read=0'), 0)

    def test_book_announcements_and_cancellation_notifications(self):
        self.login(2)
        data = dict(title='Published title', author='Author', category='ICT',
                    total_copies='1', loan_days='14', allow_hardcopy='on')
        self.post('/admin/books/new', **data)
        self.assertEqual(self.scalar('SELECT count(*) FROM notifications'), 2)
        self.post('/admin/books/3/edit', **data)
        self.assertEqual(self.scalar('SELECT count(*) FROM notifications'), 2)
        self.post('/admin/books/new', **data, restricted='on')
        self.assertEqual(self.scalar('SELECT count(*) FROM notifications'), 2)

        self.login(1)
        self.post('/notifications/read-all')
        self.assertEqual(self.scalar('SELECT count(*) FROM notifications WHERE user_id=3 AND is_read=0'), 1)
        self.post('/books/1/request')
        self.post('/books/1/request')
        self.assertEqual(self.scalar('SELECT count(*) FROM notifications WHERE user_id=2'), 1)
        self.post('/requests/1/cancel')
        self.login(2)
        self.assertIn(b'cancelled their request', self.client.get('/notifications').data)

    def test_search_and_persistence(self):
        self.login(1)
        self.assertIn(b'Test Book', self.client.get('/library?q=Test').data)
        self.assertNotIn(b'Test Book', self.client.get('/library?q=missing').data)
        self.post('/books/1/request', note='Collect tomorrow')
        other = create_app({'TESTING': True, 'DATABASE': self.database, 'SECRET_KEY': 'testing-only'}).test_client()
        with other.session_transaction() as session:
            session['user_id'] = 1
        self.assertIn(b'Pending review', other.get('/my-books').data)

if __name__ == '__main__':
    unittest.main()
