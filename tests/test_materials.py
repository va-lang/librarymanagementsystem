"""Study archive uploads, downloads, filtering and access controls."""
from io import BytesIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zipfile import ZipFile

from app import create_app


class MaterialsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'materials-test',
            'DATABASE': str(self.folder / 'test.sqlite'),
            'UPLOAD_FOLDER': str(self.folder / 'uploads'),
        })
        self.client = self.app.test_client()
        connection = sqlite3.connect(self.app.config['DATABASE'])
        with connection:
            for name, role, status in [
                ('Student', 'student', 'active'),
                ('Other Student', 'student', 'active'),
                ('Librarian', 'admin', 'active'),
                ('Suspended', 'student', 'suspended'),
            ]:
                connection.execute(
                    'INSERT INTO users(name,email,password_hash,role,status) VALUES(?,?,?,?,?)',
                    (name, name.replace(' ', '') + '@example.com', 'unused', role, status),
                )
        connection.close()
        self.login(1)

    def tearDown(self):
        self.temp.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session.clear()
            session['user_id'] = user_id
            session['csrf'] = 'materials-token'

    def upload(self, name='notes.txt', content=b'Networking lecture notes', **fields):
        data = {
            'csrf': 'materials-token',
            'title': 'Introduction to networks',
            'course': 'ICT 101',
            'material_type': 'Lecture notes',
            'file': (BytesIO(content), name),
            **fields,
        }
        return self.client.post('/materials/upload', data=data, follow_redirects=True)

    def post(self, path):
        return self.client.post(path, data={'csrf': 'materials-token'}, follow_redirects=True)

    def test_upload_browse_filter_download_and_delete(self):
        self.assertEqual(self.client.get('/materials/').status_code, 200)
        self.assertEqual(self.client.get('/materials/upload').status_code, 200)
        self.assertIn(b'Study materials', self.client.get('/library').data)
        result = self.upload()
        self.assertIn(b'Material uploaded', result.data)
        self.assertIn(b'Introduction to networks', result.data)
        self.assertIn(b'Introduction to networks', self.client.get('/materials/?q=ICT').data)
        self.assertNotIn(b'Introduction to networks', self.client.get('/materials/?q=biology').data)
        self.assertNotIn(b'Introduction to networks', self.client.get('/materials/?type=Slides').data)
        self.login(2)
        self.assertIn(b'Introduction to networks', self.client.get('/materials/').data)
        self.assertNotIn(b'Introduction to networks', self.client.get('/materials/?mine=1').data)
        with self.client.get('/materials/1/download') as response:
            self.assertEqual(response.data, b'Networking lecture notes')
            self.assertIn('attachment;', response.headers['Content-Disposition'])
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(self.post('/materials/1/delete').status_code, 403)
        self.login(1)
        self.assertIn(b'Material', self.post('/materials/1/delete').data)
        self.assertEqual(list((self.folder / 'uploads').iterdir()), [])
        self.assertEqual(self.client.get('/materials/1/download').status_code, 404)

    def test_owner_and_type_filters_with_multiple_uploaders(self):
        self.upload(title='My slides', material_type='Slides')
        self.upload(title='My lecture notes', material_type='Lecture notes')
        self.login(2)
        self.upload(title='Other slides', material_type='Slides')
        self.login(1)

        everyone = self.client.get('/materials/?mine=0').data
        for title in [b'My slides', b'My lecture notes', b'Other slides']:
            self.assertIn(title, everyone)

        mine = self.client.get('/materials/?mine=1').data
        self.assertIn(b'My slides', mine)
        self.assertIn(b'My lecture notes', mine)
        self.assertNotIn(b'Other slides', mine)

        filtered = self.client.get('/materials/?mine=1&type=Slides&q=ICT').data
        self.assertIn(b'My slides', filtered)
        self.assertNotIn(b'My lecture notes', filtered)
        self.assertNotIn(b'Other slides', filtered)

        everyone_slides = self.client.get('/materials/?mine=0&type=Slides').data
        self.assertIn(b'My slides', everyone_slides)
        self.assertIn(b'Other slides', everyone_slides)
        self.assertNotIn(b'My lecture notes', everyone_slides)

    def test_admin_can_remove_and_guests_cannot_download(self):
        self.upload()
        guest = self.app.test_client()
        for path in ['/materials/', '/materials/upload', '/materials/1/download']:
            self.assertEqual(guest.get(path).status_code, 302)
        self.login(4)
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.post('/materials/1/delete').status_code, 403)
        self.login(3)
        self.assertEqual(self.post('/materials/1/delete').status_code, 200)
        self.assertEqual(self.client.get('/materials/1/download').status_code, 404)

    def test_file_validation_and_csrf(self):
        for name, content in [
            ('page.html', b'<script>bad</script>'),
            ('fake.pdf', b'not a PDF'),
            ('fake.pptx', b'not an archive'),
            ('empty.txt', b''),
            ('binary.txt', b'\x00\xff'),
            ('notes.txt.exe', b'executable'),
        ]:
            response = self.upload(name, content)
            self.assertNotIn(b'Material uploaded', response.data)
        self.assertNotIn(b'Material uploaded', self.upload(title='').data)
        self.assertNotIn(b'Material uploaded', self.upload(course='').data)
        self.assertNotIn(b'Material uploaded', self.upload(material_type='Invalid').data)
        self.assertEqual(self.client.post('/materials/upload', data={}).status_code, 400)
        self.assertFalse((self.folder / 'uploads').exists())

    def test_supported_formats_and_safe_file_names(self):
        for extension, member in [('pptx', 'ppt/presentation.xml'), ('docx', 'word/document.xml')]:
            buffer = BytesIO()
            with ZipFile(buffer, 'w') as archive:
                archive.writestr('[Content_Types].xml', '<Types/>')
                archive.writestr(member, '<document/>')
            self.assertIn(b'Material uploaded', self.upload('lecture.' + extension, buffer.getvalue()).data)
        self.assertIn(b'Material uploaded', self.upload('../../lecture.PDF', b'%PDF-1.7\nexample').data)
        files = list((self.folder / 'uploads').iterdir())
        self.assertEqual(len(files), 3)
        self.assertTrue(all(len(file.stem) == 48 for file in files))
        with self.client.get('/materials/3/download') as response:
            self.assertNotIn('../', response.headers['Content-Disposition'])

    def test_upload_size_limit(self):
        from unittest.mock import patch
        with patch('materials.MAX_FILE_SIZE', 8):
            self.assertIn(b'too large', self.upload(content=b'0123456789').data)
        self.app.config['MAX_CONTENT_LENGTH'] = 100
        self.assertEqual(self.upload().status_code, 413)


if __name__ == '__main__':
    unittest.main()
