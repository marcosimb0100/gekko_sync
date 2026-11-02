import hashlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gekko_sync.api import ApiClient, normalize_url
from gekko_sync.engine import ReceiveEngine, safe_path
from gekko_sync.state import State
from gekko_sync import vault


class FakeApi:
    def __init__(self, rows, content=b'contents'):
        self.rows, self.content, self.ack = rows, content, False

    def json(self, method, path, data=None):
        if path.endswith('/confirmar'):
            self.ack = True
            return {}
        if method == 'POST':
            return {'manifiesto': 'snapshot'}
        return {'cambios': self.rows, 'cursor': len(self.rows), 'fin': True}

    def open(self, method, path):
        return io.BytesIO(self.content)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'documents'
        self.folder.mkdir()
        self.state = State(self.root / 'appdata')
        self.state.add_mapping('account', 'repository', 'Documents', self.folder)
        self.mapping = self.state.mappings('account')[0]

    def row(self, data=b'contents', path=None):
        return {'id': 'file', 'tipo': 'archivo', 'ruta': path or ['report.txt'], 'nombre': 'report.txt',
            'revision': 1, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'version': 'v1'}

    def test_url_requires_secure_remote_connections(self):
        self.assertEqual(normalize_url('http://localhost:7509'), 'http://localhost:7509/api')
        self.assertEqual(normalize_url('https://example.test/api/'), 'https://example.test/api')
        for value in ('http://remote.test', 'https://user:pass@example.test', 'https://example.test/api?key=x'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_url(value)

    def test_login_reuses_gekko_contract(self):
        client = ApiClient('http://localhost:7509')
        with patch.object(client, 'json', return_value={'token': 'Bearer token'}) as request:
            client.login('user@example.test', 'password')
        request.assert_called_once_with('POST', '/usuarios/acceso', {'correo_electronico': 'user@example.test', 'clave': 'password'})
        self.assertEqual(client.token, 'Bearer token')
        self.assertNotIn('password', vars(client))

    def test_path_traversal_and_reserved_paths_are_rejected(self):
        for path in (['..', 'private'], ['/absolute'], ['C:secret'], ['CON.txt'], ['a\\b'], ['.gekko-conflictos', 'x']):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_path(self.folder, path)

    def test_mapping_overlap_is_rejected_even_between_accounts(self):
        child = self.folder / 'nested'
        child.mkdir()
        with self.assertRaises(ValueError):
            self.state.add_mapping('other-account', 'other-repository', 'Other', child)

    def test_secrets_are_not_saved_as_plaintext(self):
        with patch.object(vault, 'protect', return_value=b'encrypted-by-windows'), patch.object(vault, 'unprotect', return_value='secret'):
            self.state.save_secret('account:session', 'secret')
            self.assertEqual(self.state.secret('account:session'), 'secret')
            self.assertIsNone(self.state.secret('other-account:session'))
        with self.state.connection() as db:
            self.assertEqual(db.execute('SELECT value FROM secrets').fetchone()[0], b'encrypted-by-windows')

    @unittest.skipUnless(os.name == 'nt', 'DPAPI requires Windows')
    def test_windows_dpapi_roundtrip(self):
        encrypted = vault.protect('temporary-test-token')
        self.assertNotIn(b'temporary-test-token', encrypted)
        self.assertEqual(vault.unprotect(encrypted), 'temporary-test-token')

    def test_download_is_verified_and_acknowledged(self):
        api = FakeApi([{'accion': 'actualizar', 'entrada': 'file', 'datos': self.row()}])
        ReceiveEngine(api, self.state).sync(self.mapping)
        self.assertEqual((self.folder / 'report.txt').read_bytes(), b'contents')
        self.assertTrue(api.ack)
        self.assertIn('file', self.state.entries(self.mapping['id']))

    def test_bad_hash_never_overwrites_local_file_or_acknowledges(self):
        target = self.folder / 'report.txt'
        target.write_bytes(b'original')
        api = FakeApi([{'accion': 'actualizar', 'entrada': 'file', 'datos': self.row()}], b'corrupt!')
        with self.assertRaises(ValueError):
            ReceiveEngine(api, self.state).sync(self.mapping)
        self.assertEqual(target.read_bytes(), b'original')
        self.assertFalse(api.ack)
        self.assertFalse(list(self.folder.glob('.gekko-download-*')))

    def test_local_edits_are_preserved_before_remote_replacement(self):
        (self.folder / 'report.txt').write_bytes(b'local edit')
        api = FakeApi([{'accion': 'actualizar', 'entrada': 'file', 'datos': self.row()}])
        ReceiveEngine(api, self.state).sync(self.mapping)
        self.assertEqual((self.folder / 'report.txt').read_bytes(), b'contents')
        copies = list((self.folder / '.gekko-conflictos').iterdir())
        self.assertEqual(copies[0].read_bytes(), b'local edit')

    def test_remote_delete_moves_to_recovery_instead_of_destroying(self):
        (self.folder / 'report.txt').write_bytes(b'contents')
        self.state.save_entry(self.mapping['id'], 'file', self.row())
        api = FakeApi([{'accion': 'eliminado', 'entrada': 'file'}])
        ReceiveEngine(api, self.state).sync(self.mapping)
        self.assertFalse((self.folder / 'report.txt').exists())
        self.assertEqual(next((self.folder / '.gekko-recuperados').iterdir()).read_bytes(), b'contents')
        self.assertTrue(api.ack)

    def test_access_revocation_keeps_local_copy(self):
        target = self.folder / 'report.txt'
        target.write_bytes(b'contents')
        self.state.save_entry(self.mapping['id'], 'file', self.row())
        ReceiveEngine(FakeApi([{'accion': 'retirar_acceso', 'entrada': 'file'}]), self.state).sync(self.mapping)
        self.assertEqual(target.read_bytes(), b'contents')
        self.assertNotIn('file', self.state.entries(self.mapping['id']))

    def test_missing_local_folder_never_requests_or_publishes_changes(self):
        self.folder.rmdir()
        api = FakeApi([])
        with patch.object(api, 'json') as request, self.assertRaises(ValueError):
            ReceiveEngine(api, self.state).sync(self.mapping)
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
