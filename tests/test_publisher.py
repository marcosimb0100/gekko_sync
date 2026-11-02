import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from gekko_sync.api import ApiClient, ApiError
from gekko_sync.publisher import SyncEngine
from gekko_sync.state import State


class Server:
    def __init__(self):
        self.rows, self.content, self.operations = {}, {}, []
        self.writable, self.fail_after_commit, self.conflict = True, False, False
        self.counter = 0

    def json(self, method, path, data=None):
        if '/capacidades?' in path:
            return {'acciones': ['listar', 'sync_local'] + (['editar_local'] if self.writable else []),
                    'limite_archivo_bytes': 1000000}
        if path.endswith('/confirmar'):
            return {}
        if method == 'POST':
            return {'manifiesto': 'snapshot'}
        return {'cambios': [{'accion': 'actualizar', 'entrada': key, 'datos': row.copy()}
                            for key, row in self.rows.items()], 'cursor': len(self.rows), 'fin': True}

    def operation_json(self, params, key):
        return self.write(params, key)

    def upload_operation(self, params, payload, key):
        return self.write(params, key, Path(payload).read_bytes())

    def write(self, params, key, content=None):
        self.operations.append((params.copy(), key))
        if self.conflict:
            raise ApiError('Revision distinta', 409, {'contenido_conservado': True})
        identifier = params.get('entrada')
        if identifier:
            row = self.rows[identifier]
            if params['revision'] != row['revision']:
                raise ApiError('Revision distinta', 409, {'contenido_conservado': True})
            row['revision'] += 1
        else:
            self.counter += 1
            identifier = str(self.counter)
            parent = params.get('padre')
            row = {'id': identifier, 'revision': 1, 'nombre': params['nombre'], 'padre': parent,
                   'ruta': (self.rows[parent]['ruta'] if parent else []) + [params['nombre']],
                   'tipo': 'carpeta' if params['accion'] == 'crear_carpeta' else 'archivo', 'editar_local': True}
            self.rows[identifier] = row
        if content is not None:
            row.update(sha256=hashlib.sha256(content).hexdigest(), bytes=len(content), version='v')
            self.content[identifier] = content
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise ApiError('Conexion perdida')
        return {'entrada': identifier, 'revision': row['revision']}

    def open(self, method, path):
        identifier = urlsplit(path).path.split('/')[-2]
        return io.BytesIO(self.content[identifier])


class PublishTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root / 'docs'
        self.folder.mkdir()
        self.state = State(self.root / 'state')
        self.state.add_mapping('account', 'repo', 'Documents', self.folder)
        self.mapping = self.state.mappings('account')[0]
        self.server = Server()

    def sync(self):
        SyncEngine(self.server, self.state).sync(self.mapping)

    def test_new_empty_file_is_published(self):
        (self.folder / 'Hola.txt').touch()
        self.sync()
        self.assertEqual(self.server.content['1'], b'')
        self.assertEqual(self.state.jobs(self.mapping['id'])[0]['status'], 'completa')

    def test_modified_file_creates_new_revision_without_duplicate(self):
        target = self.folder / 'Hola.txt'
        target.write_bytes(b'first')
        self.sync()
        target.write_bytes(b'edited')
        self.sync()
        self.assertEqual(len(self.server.rows), 1)
        self.assertEqual(self.server.rows['1']['revision'], 2)
        self.assertEqual(self.server.content['1'], b'edited')

    def test_nested_directories_and_file(self):
        child = self.folder / 'Parent' / 'Child'
        child.mkdir(parents=True)
        (child / 'file.txt').write_bytes(b'nested')
        self.sync()
        self.assertEqual(self.server.rows['3']['ruta'], ['Parent', 'Child', 'file.txt'])
        self.assertEqual(self.server.content['3'], b'nested')

    def test_lost_response_recovers_without_second_creation(self):
        (self.folder / 'Hola.txt').write_bytes(b'hello')
        self.server.fail_after_commit = True
        with self.assertRaises(ApiError):
            self.sync()
        job = self.state.jobs(self.mapping['id'])[0]
        self.assertEqual(job['status'], 'pendiente')
        self.assertTrue(Path(job['payload']).exists())
        self.sync()
        self.assertEqual(len(self.server.operations), 1)
        self.assertEqual(self.state.jobs(self.mapping['id'])[0]['id'], job['id'])
        self.assertFalse(Path(job['payload']).exists())

    def test_readonly_never_uploads(self):
        self.server.writable = False
        (self.folder / 'Hola.txt').write_bytes(b'hello')
        self.sync()
        self.assertEqual(self.server.operations, [])
        self.assertEqual((self.folder / 'Hola.txt').read_bytes(), b'hello')

    def test_conflict_preserves_local_content_and_receives_server(self):
        target = self.folder / 'Hola.txt'
        target.write_bytes(b'initial')
        self.sync()
        target.write_bytes(b'local edit')
        self.server.rows['1'].update(revision=2, sha256=hashlib.sha256(b'remote edit').hexdigest(), bytes=11)
        self.server.content['1'] = b'remote edit'
        self.sync()
        self.assertEqual(target.read_bytes(), b'remote edit')
        self.assertEqual(next((self.folder / '.gekko-conflictos').iterdir()).read_bytes(), b'local edit')
        self.assertEqual(self.state.jobs(self.mapping['id'])[-1]['status'], 'conflicto')

    def test_local_deletion_is_not_published(self):
        target = self.folder / 'Hola.txt'
        target.write_bytes(b'initial')
        self.sync()
        target.unlink()
        self.sync()
        self.assertEqual(len(self.server.operations), 1)
        self.assertEqual(target.read_bytes(), b'initial')

    def test_multipart_empty_file_has_correct_length_and_credentials(self):
        path = self.folder / 'empty.txt'
        path.touch()
        client = ApiClient('http://localhost:7509', 'token', 'device', 'secret')
        def perform(request):
            body = b''.join(request.data)
            self.assertEqual(int(request.get_header('Content-length')), len(body))
            self.assertIn(b'name="archivo"; filename="upload.bin"', body)
            self.assertIn('á.txt'.encode(), body)
            self.assertEqual(request.get_header('Idempotency-key'), 'stable-key')
            self.assertEqual(request.get_header('X-sync-device'), 'device')
            return io.BytesIO(json.dumps({'datos': {'entrada': 'id', 'revision': 1}}).encode())
        with patch.object(client, 'perform', side_effect=perform):
            self.assertEqual(client.upload_operation({'nombre': 'á.txt'}, path, 'stable-key')['entrada'], 'id')
