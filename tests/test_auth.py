import tempfile
import time
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

from estratto import auth
from estratto.db import Database
from estratto.profiles import ProfileStore
from estratto.webapp import create_app


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.path = self.base / 'accounts.db'
        auth.initialize(self.path)

    def register(self, email='reader@example.com'):
        return auth.authenticate(self.path, email, '012345', True, 'test')

    def assert_status(self, status, fn, *args):
        with self.assertRaises(HTTPException) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.status_code, status)

    def test_email_and_pin_validation(self):
        self.assertEqual(auth.normalize_email(' Reader@Example.com '), 'reader@example.com')
        for email in ('invalid', 'a..b@example.com', 'a@-bad.com', 'a@b', None):
            self.assert_status(400, auth.normalize_email, email)
        for pin in ('1234', '1234567', '１２３４５６', 123456, None):
            self.assert_status(400, auth.authenticate, self.path, 'a@example.com', pin, True, 'test')

    def test_returning_user_and_no_pin_overwrite(self):
        first = self.register()
        self.assert_status(409, auth.authenticate, self.path, first['email'], '111111', True, 'test')
        self.assert_status(401, auth.authenticate, self.path, first['email'], '111111', False, 'test')
        second = auth.authenticate(self.path, 'READER@example.com', '012345', False, 'test')
        self.assertNotEqual(first['token'], second['token'])
        self.assertEqual(auth.session(self.path, first['token'])['profile_secret'], auth.session(self.path, second['token'])['profile_secret'])
        with auth.connect(self.path) as db:
            self.assertNotEqual(db.execute('SELECT pin_hash FROM accounts').fetchone()[0], '012345')
            self.assertNotEqual(db.execute('SELECT token_hash FROM sessions').fetchone()[0], first['token'])

    def test_expiry_and_logout(self):
        login = self.register()
        self.assertAlmostEqual(login['expires_at'] - time.time(), 90 * 86400, delta=2)
        auth.logout(self.path, login['token'])
        self.assert_status(401, auth.session, self.path, login['token'])
        login = auth.authenticate(self.path, login['email'], '012345', False, 'test')
        with auth.connect(self.path) as db:
            db.execute('UPDATE sessions SET expires_at = 0')
        self.assert_status(401, auth.session, self.path, login['token'])

    def test_guess_limits_survive_reinitialization(self):
        self.register()
        for _ in range(5):
            self.assert_status(401, auth.authenticate, self.path, 'reader@example.com', '999999', False, 'test')
        auth.initialize(self.path)
        self.assert_status(429, auth.authenticate, self.path, 'reader@example.com', '012345', False, 'another-ip')
        with auth.connect(self.path) as db:
            db.execute('UPDATE attempts SET reset_at = 0')
        self.assertIn('token', auth.authenticate(self.path, 'reader@example.com', '012345', False, 'test'))

    def test_cleanup_preserves_account_and_metadata(self):
        login = self.register()
        account = auth.session(self.path, login['token'])
        store = ProfileStore.from_profile(self.base, account['profile_secret'])
        store.save_settings(account['profile_secret'], {'theme': 'dark'})
        (store.files_dir / 'document').write_bytes(b'file')
        (store.temp_dir / 'partial').write_bytes(b'partial')
        (store.session_dir / 'session').write_bytes(b'telegram')
        with Database(store.db_path) as db:
            db.upsert_catalog_entry(1, 'file.pdf', '', 4, '', '.pdf', source='local')
            db.mark_downloaded(1, 'local', 'file.pdf', str(store.files_dir / 'document'))
        now = time.time()
        self.assertEqual(auth.cleanup_inactive_files(self.path, self.base, now), 0)
        with auth.connect(self.path) as db:
            db.execute('UPDATE accounts SET last_seen = ?', (now - 31 * 86400,))
        self.assertEqual(auth.cleanup_inactive_files(self.path, self.base, now), 1)
        self.assertFalse(store.files_dir.exists())
        self.assertFalse(store.temp_dir.exists())
        self.assertTrue((store.session_dir / 'session').exists())
        self.assertEqual(store.load_settings(account['profile_secret']), {'theme': 'dark'})
        with Database(store.db_path) as db:
            self.assertIsNone(db.get_record(1))
            self.assertIsNotNone(db.get_catalog_entry(1))
        self.assertEqual(auth.cleanup_inactive_files(self.path, self.base, now), 0)
        self.assertIn('token', auth.authenticate(self.path, login['email'], '012345', False, 'test'))

    def test_remembered_activity_prevents_cleanup(self):
        login = self.register()
        with auth.connect(self.path) as db:
            db.execute('UPDATE accounts SET last_seen = 0')
        auth.session(self.path, login['token'])
        self.assertEqual(auth.cleanup_inactive_files(self.path, self.base), 0)

    def test_api_isolation_and_file_streaming(self):
        config = self.base / 'config.yaml'
        config.write_text('logging:\n  level: ERROR\n')
        with TestClient(create_app(str(config))) as client:
            self.assertEqual(client.get('/api/catalog', headers={'X-Estratto-Profile': 'a legacy profile phrase'}).status_code, 401)
            self.assertEqual(client.post('/api/auth/email', json={'email': 'reader@example.com'}).json()['registered'], False)
            login = client.post('/api/auth/login', json={'email': 'reader@example.com', 'pin': '012345', 'register': True})
            self.assertEqual(login.status_code, 200, login.text)
            headers = {'Authorization': 'Bearer ' + login.json()['token']}
            profile = client.get('/api/profile/status', headers=headers).json()
            upload = client.post('/api/upload/local', headers=headers, files={'file': ('test.pdf', b'%PDF-test document', 'application/pdf')})
            self.assertEqual(upload.status_code, 200, upload.text)
            doc_id = upload.json()['message_id']
            url = f'/api/file/{doc_id}'
            self.assertEqual(client.get(url, headers=headers).content, b'%PDF-test document')
            ranged = client.get(url, headers={**headers, 'Range': 'bytes=0-3'})
            self.assertEqual(ranged.status_code, 206)
            self.assertEqual(ranged.content, b'%PDF')
            other = client.post('/api/auth/login', json={'email': 'other@example.com', 'pin': '654321', 'register': True}).json()
            other_headers = {'Authorization': 'Bearer ' + other['token'], 'X-Estratto-Profile-Hash': profile['profile_hash']}
            self.assertEqual(client.get(url, headers=other_headers).status_code, 404)
            self.assertEqual(client.get(f'/api/file-manifest/{doc_id}', headers=other_headers).status_code, 404)
            self.assertEqual(client.get(f'/api/file-chunk/{doc_id}/0', headers={'X-Estratto-Profile-Hash': profile['profile_hash']}).status_code, 401)
            self.assertEqual(client.post('/api/auth/logout', headers=headers).status_code, 200)
            self.assertEqual(client.get(url, headers=headers).status_code, 401)


if __name__ == '__main__':
    unittest.main()
