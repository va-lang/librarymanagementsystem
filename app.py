"""GIMPA library: Python/Flask views and SQLite-backed library workflows."""
from datetime import date, timedelta
from functools import wraps
from pathlib import Path
import os
import secrets
import sqlite3
from urllib.parse import urlsplit

import click
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from materials import create_materials_blueprint

LABELS = {'pending': 'Pending review', 'approved': 'Ready for pickup', 'issued': 'Issued',
          'returned': 'Returned', 'rejected': 'Rejected', 'cancelled': 'Cancelled'}


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(DATABASE=str(Path(app.instance_path) / 'library.sqlite'),
                            SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                            MAX_CONTENT_LENGTH=21 * 1024 * 1024,
                            UPLOAD_FOLDER=str(Path(app.instance_path) / 'materials'))
    if config:
        app.config.update(config)

    @app.after_request
    def development_cache_control(response):
        if app.debug:
            response.headers['Cache-Control'] = 'no-store'
        return response

    Path(app.instance_path).mkdir(exist_ok=True)
    if not app.config.get('SECRET_KEY'):
        secret = os.environ.get('SECRET_KEY')
        if not secret:
            keyfile = Path(app.instance_path) / 'secret.key'
            try:
                with keyfile.open('x') as stream:
                    stream.write(secrets.token_hex(32))
                keyfile.chmod(0o600)
            except FileExistsError:
                pass
            secret = keyfile.read_text()
        app.config['SECRET_KEY'] = secret

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(app.config['DATABASE'], timeout=10)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
        return g.db

    @app.teardown_appcontext
    def close_db(_error):
        connection = g.pop('db', None)
        if connection:
            connection.close()

    with app.app_context():
        db().executescript(Path(app.root_path, 'schema.sql').read_text())
        # Existing accounts retain their permissions when adopting role IDs.
        with db():
            db().execute(
                "UPDATE users SET student_id=CASE WHEN role='admin' THEN '0' ELSE '1' END"
            )

    @app.before_request
    def load_user_and_check_csrf():
        g.user = db().execute('SELECT * FROM users WHERE id=?', (session.get('user_id'),)).fetchone()
        session.setdefault('csrf', secrets.token_hex(24))
        if request.method == 'POST' and not secrets.compare_digest(session['csrf'], request.form.get('csrf', '')):
            abort(400, 'This form expired. Reload the page and try again.')

    @app.context_processor
    def template_context():
        unread_count = 0
        if g.user:
            unread_count = db().execute(
                'SELECT count(*) FROM notifications WHERE user_id=? AND is_read=0',
                (g.user['id'],),
            ).fetchone()[0]
        return dict(user=g.user, csrf=session.get('csrf'), labels=LABELS,
                    today=date.today().isoformat(), unread_count=unread_count)

    def notify(user_id, message, destination):
        # Use the caller's transaction so notifications match committed changes.
        db().execute(
            'INSERT INTO notifications(user_id,message,destination) VALUES(?,?,?)',
            (user_id, message, destination),
        )

    def notify_admins(message):
        admins = db().execute(
            "SELECT id FROM users WHERE role='admin' AND status='active'"
        ).fetchall()
        for admin_user in admins:
            notify(admin_user['id'], message, url_for('admin', tab='requests'))

    def login_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not g.user:
                return redirect(url_for('auth'))
            return view(*args, **kwargs)
        return wrapper

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if g.user['role'] != 'admin' or g.user['status'] != 'active':
                abort(403)
            return view(*args, **kwargs)
        return wrapper

    app.register_blueprint(create_materials_blueprint(db, login_required))

    @app.get('/')
    def home():
        return render_template('index.html')

    @app.get('/about')
    def about():
        return render_template('about.html')

    @app.route('/auth', methods=['GET', 'POST'])
    def auth():
        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            person = db().execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            if person and check_password_hash(person['password_hash'], password):
                session.clear()
                session['user_id'] = person['id']
                return redirect(url_for('library'))
            else:
                flash('Email or password is incorrect.', 'error')
        return render_template('auth.html')

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('home'))

    @app.get('/library')
    @login_required
    def library():
        q = request.args.get('q', '').strip()
        category = request.args.get('category', '')
        books = db().execute('SELECT * FROM books WHERE (? OR restricted=0) ORDER BY title', (g.user['role'] == 'admin',)).fetchall()
        categories = sorted({b['category'] for b in books})
        shown = [b for b in books if (not category or b['category'] == category) and q.lower() in f"{b['title']} {b['author']} {b['isbn']}".lower()]
        return render_template('library.html', books=shown, total=len(books), categories=categories, q=q, category=category)

    @app.get('/notifications')
    @login_required
    def notifications():
        rows = db().execute(
            'SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC',
            (g.user['id'],),
        ).fetchall()
        return render_template('notifications.html', notifications=rows)

    @app.post('/notifications/<int:notification_id>/open')
    @login_required
    def open_notification(notification_id):
        row = db().execute(
            'SELECT * FROM notifications WHERE id=? AND user_id=?',
            (notification_id, g.user['id']),
        ).fetchone()
        if not row:
            abort(404)
        with db():
            db().execute(
                'UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?',
                (notification_id, g.user['id']),
            )
        return redirect(row['destination'])

    @app.post('/notifications/read-all')
    @login_required
    def read_all_notifications():
        with db():
            db().execute(
                'UPDATE notifications SET is_read=1 WHERE user_id=? AND is_read=0',
                (g.user['id'],),
            )
        flash('All notifications marked as read.', 'success')
        return redirect(url_for('notifications'))

    def accessible_book(book_id):
        book = db().execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone()
        if not book or (book['restricted'] and g.user['role'] != 'admin'):
            abort(404)
        return book

    @app.get('/books/<int:book_id>')
    @login_required
    def book_detail(book_id):
        return render_template('book.html', book=accessible_book(book_id))

    @app.post('/books/<int:book_id>/request')
    @login_required
    def request_book(book_id):
        with db():
            db().execute('BEGIN IMMEDIATE')
            book = accessible_book(book_id)
            active = db().execute("SELECT book_id FROM requests WHERE user_id=? AND status IN ('pending','approved','issued')", (g.user['id'],)).fetchall()
            if g.user['status'] != 'active' or not g.user['can_request'] or not book['allow_hardcopy']:
                flash('Hardcopy requests are not available for this account or title.', 'error')
            elif any(r['book_id'] == book_id for r in active):
                flash('You already have an active request for this book.', 'error')
            elif len(active) >= g.user['max_loans']:
                flash('You have reached your active loan and request limit.', 'error')
            else:
                db().execute('INSERT INTO requests(user_id,book_id,note) VALUES(?,?,?)', (g.user['id'], book_id, request.form.get('note', '').strip()[:1000]))
                notify_admins(f"{g.user['name']} requested {book['title']}.")
                flash('Request sent. Track its progress in My books.', 'success')
        return redirect(url_for('my_books'))

    @app.get('/books/<int:book_id>/read')
    @login_required
    def read_book(book_id):
        book = accessible_book(book_id)
        if not book['allow_softcopy'] or not book['read_url']:
            abort(404)
        return redirect(book['read_url'])

    def all_requests(user_id=None):
        sql = 'SELECT r.*, b.title, b.author, u.name FROM requests r JOIN books b ON r.book_id=b.id JOIN users u ON r.user_id=u.id'
        return db().execute(sql + (' WHERE r.user_id=?' if user_id else '') + ' ORDER BY r.id DESC', (user_id,) if user_id else ()).fetchall()

    @app.get('/my-books')
    @login_required
    def my_books():
        rows = all_requests(g.user['id'])
        stats = [('Active requests', sum(r['status'] in ('pending','approved') for r in rows)),
                 ('Books with you', sum(r['status'] == 'issued' for r in rows)),
                 ('Overdue', sum(r['status'] == 'issued' and bool(r['due_date']) and r['due_date'] < date.today().isoformat() for r in rows)),
                 ('Returned', sum(r['status'] == 'returned' for r in rows))]
        return render_template('requests.html', rows=rows, stats=stats)

    @app.post('/requests/<int:request_id>/cancel')
    @login_required
    def cancel_request(request_id):
        with db():
            db().execute('BEGIN IMMEDIATE')
            row = db().execute('SELECT * FROM requests WHERE id=? AND user_id=?', (request_id, g.user['id'])).fetchone()
            if not row:
                abort(404)
            if row['status'] not in ('pending', 'approved'):
                abort(400, 'Only pending or approved requests can be cancelled.')
            if row['status'] == 'approved':
                db().execute('UPDATE books SET available_copies=available_copies+1 WHERE id=?', (row['book_id'],))
            db().execute("UPDATE requests SET status='cancelled' WHERE id=?", (request_id,))
            book = db().execute('SELECT title FROM books WHERE id=?', (row['book_id'],)).fetchone()
            notify_admins(f"{g.user['name']} cancelled their request for {book['title']}.")
        flash('Request cancelled.', 'success')
        return redirect(url_for('my_books'))

    @app.get('/admin')
    @admin_required
    def admin():
        tab = request.args.get('tab', 'reports')
        books = db().execute('SELECT * FROM books ORDER BY title').fetchall()
        users = db().execute(
            'SELECT * FROM users ORDER BY (id=?) DESC, name COLLATE NOCASE, id',
            (g.user['id'],),
        ).fetchall()
        rows = all_requests()
        stats = [('Titles', len(books)), ('Copies on shelf', sum(b['available_copies'] for b in books)),
                 ('Users', len(users)), ('Pending requests', sum(r['status'] == 'pending' for r in rows)),
                 ('Books issued', sum(r['status'] == 'issued' for r in rows)),
                 ('Overdue', sum(r['status'] == 'issued' and bool(r['due_date']) and r['due_date'] < date.today().isoformat() for r in rows))]
        popular = db().execute('SELECT b.title, count(*) AS count FROM requests r JOIN books b ON b.id=r.book_id GROUP BY b.id ORDER BY count DESC LIMIT 5').fetchall()
        return render_template('admin.html', tab=tab, books=books, users=users, rows=rows, stats=stats, popular=popular)

    @app.post('/admin/requests/<int:request_id>')
    @admin_required
    def update_request(request_id):
        target = request.form.get('status')
        transitions = {'pending': ('approved','issued','rejected'), 'approved': ('issued','rejected'), 'issued': ('returned',)}
        with db():
            db().execute('BEGIN IMMEDIATE')
            row = db().execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
            if not row:
                abort(404)
            if target not in transitions.get(row['status'], ()):
                abort(400, 'This request has changed or the transition is invalid.')
            book = db().execute('SELECT * FROM books WHERE id=?', (row['book_id'],)).fetchone()
            person = db().execute('SELECT * FROM users WHERE id=?', (row['user_id'],)).fetchone()
            if target in ('approved','issued') and (person['status'] != 'active' or not person['can_request'] or not book['allow_hardcopy'] or book['restricted']):
                flash('The member or book is not eligible for borrowing.', 'error')
                return redirect(url_for('admin', tab='requests'))
            if row['status'] == 'pending' and target in ('approved','issued'):
                if book['available_copies'] < 1:
                    flash('No copies are on shelf. Keep this request pending until a copy is returned.', 'error')
                    return redirect(url_for('admin', tab='requests'))
                db().execute('UPDATE books SET available_copies=available_copies-1 WHERE id=?', (book['id'],))
            if target == 'returned' or (row['status'] == 'approved' and target == 'rejected'):
                db().execute('UPDATE books SET available_copies=available_copies+1 WHERE id=?', (book['id'],))
            due = (date.today() + timedelta(days=book['loan_days'])).isoformat() if target == 'issued' else row['due_date']
            db().execute('UPDATE requests SET status=?, due_date=?, admin_note=? WHERE id=?', (target, due, request.form.get('admin_note', '').strip()[:1000], request_id))
            message = f"{book['title']}: {LABELS[target]}."
            if target == 'issued':
                message += f" Due {due}."
            notify(row['user_id'], message, url_for('my_books'))
        flash('Request updated.', 'success')
        return redirect(url_for('admin', tab='requests'))

    @app.route('/admin/books/new', methods=['GET','POST'])
    @app.route('/admin/books/<int:book_id>/edit', methods=['GET','POST'])
    @admin_required
    def edit_book(book_id=None):
        book = db().execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone() if book_id else None
        if book_id and not book:
            abort(404)
        if request.method == 'POST':
            try:
                data = {k: request.form.get(k, '').strip() for k in ('title','author','category','isbn','description','read_url')}
                if not data['title'] or not data['author']:
                    raise ValueError('Title and author are required.')
                if data['read_url'] and (urlsplit(data['read_url']).scheme not in ('http','https') or not urlsplit(data['read_url']).netloc):
                    raise ValueError('Use a full HTTP or HTTPS softcopy URL.')
                data['total_copies'] = int(request.form.get('total_copies', '1'))
                data['loan_days'] = int(request.form.get('loan_days', '14'))
                if data['total_copies'] < 0 or data['loan_days'] < 1:
                    raise ValueError('Copies must be zero or greater and loan days at least one.')
                for k in ('allow_softcopy','allow_hardcopy','restricted'):
                    data[k] = int(k in request.form)
                with db():
                    db().execute('BEGIN IMMEDIATE')
                    held = db().execute("SELECT count(*) FROM requests WHERE book_id=? AND status IN ('approved','issued')", (book_id,)).fetchone()[0] if book_id else 0
                    if data['total_copies'] < held:
                        raise ValueError('Total copies cannot be less than reserved and issued copies.')
                    data['available_copies'] = data['total_copies'] - held
                    if book_id:
                        db().execute('UPDATE books SET '+','.join(k+'=?' for k in data)+' WHERE id=?', (*data.values(), book_id))
                    else:
                        db().execute('INSERT INTO books('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')', tuple(data.values()))
                    newly_visible = not data['restricted'] and (not book or book['restricted'])
                    if newly_visible:
                        students = db().execute(
                            "SELECT id FROM users WHERE role='student' AND status='active'"
                        ).fetchall()
                        for student in students:
                            notify(student['id'], f"New in the catalogue: {data['title']}.",
                                   url_for('library'))
                if data['restricted']:
                    flash('Book saved. It is restricted and hidden from the student catalogue.', 'success')
                else:
                    flash('Book saved and published to the student catalogue.', 'success')
                return redirect(url_for('admin', tab='books'))
            except ValueError as error:
                flash(str(error), 'error')
            book = request.form
        return render_template('book_form.html', book=book)

    @app.post('/admin/books/<int:book_id>/delete')
    @admin_required
    def delete_book(book_id):
        try:
            with db():
                db().execute('DELETE FROM books WHERE id=?', (book_id,))
            flash('Book deleted.', 'success')
        except sqlite3.IntegrityError:
            flash('This book has borrowing history. Mark it restricted instead of deleting it.', 'error')
        return redirect(url_for('admin', tab='books'))

    @app.route('/admin/users/new', methods=['GET', 'POST'])
    @admin_required
    def add_user():
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            student_id = request.form.get('student_id', '').strip()
            email_parts = email.split('@')
            valid_email = (
                len(email_parts) == 2
                and all(email_parts)
                and not any(character.isspace() for character in email)
                and '.' in email_parts[1]
            )
            if not name or not valid_email or len(password) < 8:
                flash('Enter a name, valid email and password of at least 8 characters.', 'error')
            elif student_id not in ('0', '1'):
                flash('Select a student/staff ID: 1 for Student or 0 for Admin.', 'error')
            else:
                role = 'admin' if student_id == '0' else 'student'
                try:
                    with db():
                        db().execute(
                            'INSERT INTO users(name,email,password_hash,student_id,role) VALUES(?,?,?,?,?)',
                            (name, email, generate_password_hash(password), student_id, role),
                        )
                    flash(f'Account created for {name}. They can now sign in.', 'success')
                    return redirect(url_for('admin', tab='users'))
                except sqlite3.IntegrityError:
                    flash('An account with this email already exists.', 'error')
        return render_template('user_form.html')

    @app.post('/admin/users/<int:user_id>')
    @admin_required
    def update_user(user_id):
        if user_id == g.user['id']:
            abort(400, 'You cannot change your own administrator permissions here.')
        student_id = request.form.get('student_id', '')
        role = {'0': 'admin', '1': 'student'}.get(student_id)
        status = request.form.get('status')
        try:
            limit = int(request.form.get('max_loans', '3'))
            if limit < 0 or role not in ('student','admin') or status not in ('active','suspended'):
                raise ValueError
        except ValueError:
            abort(400, 'Invalid member settings.')
        with db():
            db().execute('UPDATE users SET role=?,student_id=?,status=?,can_request=?,max_loans=? WHERE id=?', (role,student_id,status,int('can_request' in request.form),limit,user_id))
        flash('Member settings saved.', 'success')
        return redirect(url_for('admin', tab='users'))

    @app.cli.command('create-admin')
    @click.option('--email', prompt=True)
    @click.option('--name', prompt=True)
    @click.password_option(confirmation_prompt=True)
    def create_admin(email, name, password):
        """Create a librarian account with staff ID 0."""
        if len(password) < 8:
            raise click.ClickException('Use a password of at least 8 characters.')
        try:
            with db():
                db().execute("INSERT INTO users(name,email,password_hash,role,student_id) VALUES(?,?,?,'admin','0')", (name,email.strip().lower(),generate_password_hash(password)))
        except sqlite3.IntegrityError:
            raise click.ClickException('An account with that email already exists.') from None
        click.echo('Librarian account created.')

    @app.cli.command('seed-demo')
    def seed_demo():
        """Add a small catalogue only when no books exist."""
        if db().execute('SELECT count(*) FROM books').fetchone()[0]:
            click.echo('Catalogue is not empty; no books added.')
            return
        titles = [('Pride and Prejudice','Jane Austen','Fiction','https://www.gutenberg.org/ebooks/1342'),
                  ('A Tale of Two Cities','Charles Dickens','Fiction','https://www.gutenberg.org/ebooks/98'),
                  ('The Adventures of Sherlock Holmes','Arthur Conan Doyle','Mystery','https://www.gutenberg.org/ebooks/1661'),
                  ('Introduction to Information Systems','GIMPA Study Collection','ICT',''),
                  ('Principles of Management','GIMPA Study Collection','Management','')]
        with db():
            db().executemany('INSERT INTO books(title,author,category,read_url,total_copies,available_copies) VALUES(?,?,?,?,3,3)', titles)
        click.echo('Five demonstration titles added; study collection entries are sample catalogue records.')

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def error_page(error):
        return render_template('error.html', error=error), error.code

    return app


if __name__ == '__main__':
    # Local development: reload saved Python and template changes.
    app = create_app({'DEBUG': True, 'TEMPLATES_AUTO_RELOAD': True})
    app.run(host='127.0.0.1', port=8000, use_reloader=True, use_debugger=False)
