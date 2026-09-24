import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from estratto import auth
from estratto.webapp import create_app
from estratto.profiles import ProfileStore


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        config = self.base / 'config.yaml'
        config.write_text('logging:\n  level: ERROR\n')
        self.client = TestClient(create_app(str(config)))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {}
        for name in ('sender', 'reader', 'stranger'):
            login = self.client.post('/api/auth/login', json={
                'email': f'{name}@example.com', 'pin': '012345', 'register': True,
            })
            self.assertEqual(login.status_code, 200, login.text)
            self.headers[name] = {'Authorization': 'Bearer ' + login.json()['token']}
        self.payload = b'%PDF-shared file contents'
        upload = self.client.post('/api/upload/local', headers=self.headers['sender'],
                                  files={'file': ('reading.pdf', self.payload, 'application/pdf')})
        self.assertEqual(upload.status_code, 200, upload.text)
        self.file_id = upload.json()['message_id']

    def share(self, email='reader@example.com', sender='sender'):
        return self.client.post(f'/api/share/{self.file_id}', headers=self.headers[sender], json={'email': email})

    def catalog(self, name):
        return self.client.get('/api/catalog?downloaded_only=true', headers=self.headers[name]).json()['items']

    def test_delivery_history_and_duplicate_shares(self):
        response = self.share(' READER@Example.com ')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'shared')
        items = self.catalog('reader')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['filename'], 'reading.pdf')
        self.assertEqual(items[0]['source'], 'shared')
        self.assertEqual(items[0]['caption'], 'Shared by sender@example.com')
        copied_id = items[0]['message_id']
        self.assertEqual(self.client.get(f'/api/file/{copied_id}', headers=self.headers['reader']).content, self.payload)
        self.assertEqual(self.client.get(f'/api/file/{copied_id}', headers=self.headers['stranger']).status_code, 404)
        self.assertEqual(self.share().json()['status'], 'already_shared')
        self.assertEqual(len(self.catalog('reader')), 1)
        self.assertEqual(self.client.get('/api/shares/recipients', headers=self.headers['sender']).json()['recipients'], ['reader@example.com'])
        self.assertEqual(self.client.get('/api/shares/recipients', headers=self.headers['stranger']).json()['recipients'], [])
        self.assertEqual(self.client.get('/api/shares/recipients').status_code, 401)
        # An encrypted copy remains readable after its original is deleted.
        deleted = self.client.post(f'/api/delete/{self.file_id}', headers=self.headers['sender'])
        self.assertTrue(deleted.json()['deleted_paths'])
        self.assertEqual(self.client.get(f'/api/file/{copied_id}', headers=self.headers['reader']).content, self.payload)

    def test_validation_and_ownership(self):
        for email, status in [('bad-email', 400), ('sender@example.com', 400), ('unknown@example.com', 404)]:
            response = self.share(email)
            self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.share(sender='stranger').status_code, 404)
        self.assertEqual(self.catalog('reader'), [])
        self.assertEqual(self.client.post(f'/api/share/{self.file_id}', json={'email': 'reader@example.com'}).status_code, 401)
        self.assertEqual(self.client.get('/api/shares/recipients', headers=self.headers['sender']).json()['recipients'], [])

    def test_failed_copy_leaves_no_delivery_or_history(self):
        with patch.object(ProfileStore, 'encrypt_file', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.share()
        self.assertEqual(self.catalog('reader'), [])
        self.assertEqual(self.client.get('/api/shares/recipients', headers=self.headers['sender']).json()['recipients'], [])
        self.assertEqual(list(self.base.glob('profiles/*/tmp/*')), [])
        self.assertEqual(self.share().json()['status'], 'shared')

    def test_reshare_after_recipient_cleanup(self):
        self.assertEqual(self.share().status_code, 200)
        copied_id = self.catalog('reader')[0]['message_id']
        with auth.connect(self.base / 'accounts.db') as db:
            db.execute("UPDATE accounts SET last_seen = ? WHERE email = 'reader@example.com'", (time.time() - 31 * 86400,))
        self.assertEqual(auth.cleanup_inactive_files(self.base / 'accounts.db', self.base), 1)
        self.assertEqual(self.catalog('reader'), [])
        self.assertEqual(self.share().json()['status'], 'shared')
        self.assertEqual(len(self.catalog('reader')), 1)
        self.assertEqual(self.client.get(f'/api/file/{copied_id}', headers=self.headers['reader']).content, self.payload)


if __name__ == '__main__':
    unittest.main()
