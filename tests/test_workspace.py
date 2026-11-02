import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gekko_sync.state import State
from gekko_sync.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.root = self.path / 'aca'
        self.root.mkdir()
        self.state = State(self.path / 'state')
        self.workspace = Workspace(self.state, 'account')
        self.repos = [{'_id': 'repo1', 'nombre': 'Juridico'}, {'_id': 'repo2', 'nombre': 'Contabilidad'}]

    def test_creates_multiple_managed_children(self):
        rows = self.workspace.configure(self.root, self.repos)
        self.assertEqual({Path(row['folder']).name for row in rows}, {'Juridico', 'Contabilidad'})
        for row in rows:
            self.assertEqual(self.workspace.validate_repository(row['repository']), Path(row['folder']))

    def test_does_not_adopt_existing_directory_or_read_user_content(self):
        user = self.root / 'Juridico'
        user.mkdir()
        private = user / 'private.txt'
        private.write_bytes(b'private')
        rows = self.workspace.configure(self.root, self.repos[:1])
        self.assertNotEqual(Path(rows[0]['folder']), user)
        self.assertEqual(private.read_bytes(), b'private')

    def test_same_names_have_distinct_stable_folders_by_id(self):
        repos = [{'_id': 'a', 'nombre': 'Same'}, {'_id': 'b', 'nombre': 'Same'}]
        initial = self.workspace.configure(self.root, repos)
        again = self.workspace.configure(self.root, list(reversed(repos)))
        self.assertEqual(initial, again)
        self.assertEqual(len({row['folder'] for row in initial}), 2)

    def test_rename_on_server_does_not_change_registered_path(self):
        first = self.workspace.configure(self.root, self.repos[:1])[0]
        changed = self.workspace.configure(self.root, [{'_id': 'repo1', 'nombre': 'Renamed'}])[0]
        self.assertEqual(first['folder'], changed['folder'])

    def test_only_registered_paths_are_passed_to_sync_engine(self):
        self.workspace.configure(self.root, self.repos)
        (self.root / 'My stuff').mkdir()
        (self.root / 'unmanaged.txt').write_bytes(b'private')
        client = Mock()
        client.repositories.return_value = self.repos
        with patch('gekko_sync.workspace.SyncEngine') as engine:
            self.workspace.sync(client)
        calls = engine.return_value.sync.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual({Path(call.args[0]['folder']).name for call in calls}, {'Juridico', 'Contabilidad'})
        self.assertNotIn(self.root, {Path(call.args[0]['folder']) for call in calls})

    def test_newly_authorized_repository_is_created_next_cycle(self):
        self.workspace.configure(self.root, self.repos[:1])
        client = Mock()
        client.repositories.return_value = self.repos
        with patch('gekko_sync.workspace.SyncEngine'):
            self.workspace.sync(client)
        self.assertEqual(len(self.state.mappings('account')), 2)

    def test_revoked_repository_not_scanned_or_deleted(self):
        rows = self.workspace.configure(self.root, self.repos)
        client = Mock()
        client.repositories.return_value = self.repos[:1]
        with patch('gekko_sync.workspace.SyncEngine') as engine:
            self.workspace.sync(client)
        self.assertEqual(engine.return_value.sync.call_count, 1)
        self.assertTrue(Path(rows[1]['folder']).exists())

    def test_missing_identity_never_adopts_recreated_directory(self):
        row = self.workspace.configure(self.root, self.repos[:1])[0]
        (Path(row['folder']) / '.gekko-repository.json').unlink()
        with self.assertRaises(ValueError):
            self.workspace.validate_repository(row['repository'])

    def test_legacy_migration_moves_only_tracked_content_and_keeps_state(self):
        self.state.add_mapping('account', 'repo1', 'Juridico', self.root)
        mapping = self.state.mappings('account')[0]
        (self.root / 'Templates').mkdir()
        tracked = self.root / 'Templates' / 'known.txt'
        tracked.write_bytes(b'local edit')
        (self.root / 'Templates' / 'untracked.txt').write_bytes(b'private')
        (self.root / 'other.txt').write_bytes(b'other')
        self.state.save_entry(mapping['id'], 'folder', {'id': 'folder', 'tipo': 'carpeta', 'ruta': ['Templates']})
        self.state.save_entry(mapping['id'], 'file', {'id': 'file', 'tipo': 'archivo', 'ruta': ['Templates', 'known.txt'], 'revision': 5})
        rows = self.workspace.configure(self.root, self.repos)
        migrated = next(row for row in rows if row['repository'] == 'repo1')
        self.assertEqual(migrated['id'], mapping['id'])
        self.assertEqual((Path(migrated['folder']) / 'Templates' / 'known.txt').read_bytes(), b'local edit')
        self.assertEqual((self.root / 'Templates' / 'untracked.txt').read_bytes(), b'private')
        self.assertTrue((self.root / 'other.txt').exists())
        self.assertEqual(self.state.entries(mapping['id'])['file']['revision'], 5)

    def test_migration_resumes_after_process_interrupt(self):
        self.state.add_mapping('account', 'repo1', 'Juridico', self.root)
        mapping = self.state.mappings('account')[0]
        for name in ['a', 'b']:
            (self.root / name).write_text(name)
            self.state.save_entry(mapping['id'], name, {'tipo': 'archivo', 'ruta': [name]})
        import os
        rename = os.rename
        count = 0
        def interrupt(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('interrupted')
            return rename(source, target)
        with patch('gekko_sync.workspace.os.rename', side_effect=interrupt), self.assertRaises(OSError):
            self.workspace.configure(self.root, self.repos)
        rows = self.workspace.configure(self.root, self.repos)
        migrated = next(row for row in rows if row['repository'] == 'repo1')
        self.assertEqual((Path(migrated['folder']) / 'a').read_text(), 'a')
        self.assertEqual((Path(migrated['folder']) / 'b').read_text(), 'b')

    def test_repository_error_does_not_stop_other_repositories(self):
        self.workspace.configure(self.root, self.repos)
        client = Mock()
        client.repositories.return_value = self.repos
        with patch('gekko_sync.workspace.SyncEngine') as engine:
            engine.return_value.sync.side_effect = [ValueError('local failure'), None]
            self.workspace.sync(client)
        self.assertEqual(engine.return_value.sync.call_count, 2)

    def test_invalid_windows_name_gets_safe_folder(self):
        rows = self.workspace.configure(self.root, [{'_id': 'id', 'nombre': 'CON'}])
        self.assertNotEqual(Path(rows[0]['folder']).name, 'CON')

    def test_real_engine_uploads_managed_file_but_ignores_container_content(self):
        from test_publisher import Server
        rows = self.workspace.configure(self.root, self.repos[:1])
        user = self.root / 'My stuff'
        user.mkdir()
        (user / 'private.txt').write_bytes(b'private')
        (self.root / 'root-file.txt').write_bytes(b'private too')
        (Path(rows[0]['folder']) / 'public.txt').write_bytes(b'publish me')
        server = Server()
        server.repositories = lambda: self.repos[:1]
        self.workspace.sync(server)
        self.assertEqual(len(server.operations), 1)
        self.assertEqual(server.operations[0][0]['nombre'], 'public.txt')
        self.assertEqual((user / 'private.txt').read_bytes(), b'private')

    def test_missing_marker_skips_only_that_repository(self):
        rows = self.workspace.configure(self.root, self.repos)
        (Path(rows[0]['folder']) / '.gekko-repository.json').unlink()
        client = Mock()
        client.repositories.return_value = self.repos
        with patch('gekko_sync.workspace.SyncEngine') as engine:
            self.workspace.sync(client)
        self.assertEqual(engine.return_value.sync.call_count, 1)
