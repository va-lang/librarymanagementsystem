# GIMPA Library — Python web application

The application uses **Flask (Python), Jinja HTML templates and SQLite**. The design and workflow are based on the GIMPA Library Hub reference. No Node, React or Supabase setup is needed.

## Run on macOS / Linux

From this project folder:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m flask --app app seed-demo
python -m flask --app app create-admin
python app.py
```

## Run on Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m flask --app app seed-demo
.\.venv\Scripts\python.exe -m flask --app app create-admin
.\.venv\Scripts\python.exe app.py
```

Open **http://localhost:8000/**. Stop with Ctrl+C. On later runs, just run `python app.py` in the activated environment (or the Windows executable command above). Use the Flask server, not `python -m http.server`: the pages now depend on Python routes and templates.

`seed-demo` is optional and adds five sample titles only to an empty catalogue. `create-admin` prompts for the librarian's name, email and password; passwords are entered privately. Student accounts are created using Sign up. No default passwords are included.

## Seeing your changes

`python app.py` starts local development mode: saved Python changes restart the server, HTML templates reload on the next request, and responses disable browser caching. Save the file, wait for any server restart, then refresh the browser. The browser does not refresh automatically.

Edit pages in `templates/` and styles in `static/style.css`. After changing the startup configuration, stop the old server with Ctrl+C and run `python app.py` again. If an older stylesheet is still cached, hard refresh once: Cmd+Shift+R on Mac or Ctrl+F5 on Windows.

## Workflow

- Public welcome page, sign-up/sign-in and project information.
- Catalogue search by title, author or ISBN, with category filtering.
- Book details, external softcopy links, hardcopy requests and a pending waitlist when copies are unavailable.
- My books: request history, due dates, overdue status and cancellation of pending/approved requests.
- Notifications: the header bell shows unread updates. Students receive announcements for newly published books and changes to their requests; active librarians receive new-request and cancellation updates. Open an update to mark it read, or mark all as read. Notifications persist in SQLite and appear on the next page load or refresh; they are in-app updates, not email or push alerts.
- Librarian reports; approve, reject, issue and return books; add/edit/delete titles; manage member roles, suspension, request permission and loan limits.
- Approval reserves a copy. Issuing an approved request uses that reservation; issuing a pending request reserves a copy directly. Cancellation/rejection of an approval and returns restore a copy. A waitlisted request remains pending for a librarian to approve when stock becomes available.
- Python checks permissions, request ownership, duplicate requests, loan limits, copy counts and valid status transitions. Updates run in SQLite transactions. Forms use CSRF tokens; passwords are hashed.

## Project layout

- `app.py` — Python routes, authentication and library rules
- `schema.sql` — SQLite schema
- `templates/` — server-rendered pages
- `static/style.css` — shared responsive navy/gold styling
- `tests/test_app.py` — isolated workflow tests
- `instance/` — local database and signing key; ignored by Git

Each computer gets its own SQLite database. Share the source folder without `.venv/` or `instance/`, then run setup on the other computer. Google Fonts are optional; local font fallbacks and CSS-rendered covers work offline. Real catalogue entries and softcopy links can be entered by a librarian.

## Tests

```sh
python -m unittest discover -s tests -v
```

This is a local coursework application. Email verification, password recovery, uploaded ebook storage and public production hosting are not implemented. Online reading opens the external link entered by a librarian. The development server binds to localhost.
