"""Shared study materials, stored privately and served to signed-in users."""

from io import BytesIO
from pathlib import Path
import secrets
from zipfile import BadZipFile, ZipFile

from flask import (
    Blueprint, abort, current_app, flash, g, redirect, render_template,
    request, send_from_directory, url_for,
)
from werkzeug.utils import secure_filename


MAX_FILE_SIZE = 20 * 1024 * 1024
MATERIAL_TYPES = ('Slides', 'Lecture notes', 'Teaching material', 'Other')
EXTENSIONS = {'.pdf', '.pptx', '.docx', '.txt'}


def validate_file(filename, content):
    """Check size, extension and document structure without extracting archives."""
    extension = Path(filename).suffix.lower()
    if extension not in EXTENSIONS:
        raise ValueError('Choose a PDF, PPTX, DOCX or TXT file.')
    if not content:
        raise ValueError('The file is empty. Choose a file with content.')
    if len(content) > MAX_FILE_SIZE:
        raise ValueError('The file is too large. The maximum size is 20 MB.')
    if extension == '.pdf' and not content.startswith(b'%PDF-'):
        raise ValueError('This file does not appear to be a PDF.')
    if extension == '.txt':
        try:
            content.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('Text files must use UTF-8 encoding.') from None
        if b'\x00' in content:
            raise ValueError('Choose a plain text file, not a binary file.')
    if extension in ('.pptx', '.docx'):
        try:
            with ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
                required = 'ppt/presentation.xml' if extension == '.pptx' else 'word/document.xml'
                if '[Content_Types].xml' not in names or required not in names:
                    raise ValueError('The document format does not match its file extension.')
                if any(name.lower().endswith('vbaproject.bin') for name in names):
                    raise ValueError('Save the document without macros before uploading.')
        except BadZipFile:
            raise ValueError('This Office document is damaged or has an incorrect file extension.') from None
    return extension


def create_materials_blueprint(db, login_required):
    blueprint = Blueprint('materials', __name__, url_prefix='/materials')

    @blueprint.get('/')
    @login_required
    def index():
        query = request.args.get('q', '').strip()
        kind = request.args.get('type', '')
        mine = request.args.get('mine') == '1'
        clauses, parameters = [], []
        if query:
            clauses.append('(instr(lower(m.title), lower(?)) > 0 OR '
                           'instr(lower(m.course), lower(?)) > 0 OR '
                           'instr(lower(u.name), lower(?)) > 0)')
            parameters.extend([query] * 3)
        if kind:
            clauses.append('m.material_type=?')
            parameters.append(kind)
        if mine:
            clauses.append('m.user_id=?')
            parameters.append(g.user['id'])
        sql = 'SELECT m.*, u.name AS uploader FROM materials m JOIN users u ON u.id=m.user_id'
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        rows = db().execute(sql + ' ORDER BY m.id DESC', parameters).fetchall()
        return render_template(
            'materials.html', materials=rows, material_types=MATERIAL_TYPES,
            q=query, kind=kind, mine=mine,
        )

    @blueprint.route('/upload', methods=['GET', 'POST'])
    @login_required
    def upload():
        if g.user['status'] != 'active':
            abort(403, 'Your account must be active to upload study materials.')
        if request.method == 'POST':
            title = request.form.get('title', '').strip()
            course = request.form.get('course', '').strip()
            kind = request.form.get('material_type', '')
            description = request.form.get('description', '').strip()
            file = request.files.get('file')
            try:
                if not title or not course or kind not in MATERIAL_TYPES:
                    raise ValueError('Enter a title, course or subject, and material type.')
                if len(title) > 160 or len(course) > 120 or len(description) > 2000:
                    raise ValueError('Keep the title under 160 characters, course under 120, and description under 2,000.')
                if not file or not file.filename:
                    raise ValueError('Choose a file to upload.')
                filename = secure_filename(file.filename)
                content = file.stream.read(MAX_FILE_SIZE + 1)
                extension = validate_file(filename, content)
            except ValueError as error:
                flash(str(error), 'error')
            else:
                folder = Path(current_app.config['UPLOAD_FOLDER'])
                folder.mkdir(parents=True, exist_ok=True)
                stored_name = secrets.token_hex(24) + extension
                destination = folder / stored_name
                try:
                    with destination.open('xb') as stream:
                        stream.write(content)
                    with db():
                        db().execute(
                            'INSERT INTO materials '
                            '(user_id,title,course,material_type,description,original_name,stored_name,file_size) '
                            'VALUES(?,?,?,?,?,?,?,?)',
                            (g.user['id'], title, course, kind, description, filename, stored_name, len(content)),
                        )
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
                flash('Material uploaded. It is now available to everyone signed in to the archive.', 'success')
                return redirect(url_for('materials.index'))
        return render_template('material_form.html', material_types=MATERIAL_TYPES)

    @blueprint.get('/<int:material_id>/download')
    @login_required
    def download(material_id):
        material = db().execute('SELECT * FROM materials WHERE id=?', (material_id,)).fetchone()
        if not material:
            abort(404)
        response = send_from_directory(
            current_app.config['UPLOAD_FOLDER'], material['stored_name'],
            as_attachment=True, download_name=material['original_name'],
            mimetype='application/octet-stream',
        )
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @blueprint.post('/<int:material_id>/delete')
    @login_required
    def delete(material_id):
        material = db().execute('SELECT * FROM materials WHERE id=?', (material_id,)).fetchone()
        if not material:
            abort(404)
        if g.user['status'] != 'active' or (
            material['user_id'] != g.user['id'] and g.user['role'] != 'admin'
        ):
            abort(403)
        with db():
            db().execute('DELETE FROM materials WHERE id=?', (material_id,))
        (Path(current_app.config['UPLOAD_FOLDER']) / material['stored_name']).unlink(missing_ok=True)
        flash('Study material removed.', 'success')
        return redirect(url_for('materials.index'))

    return blueprint
