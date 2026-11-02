import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gekko_sync.watcher import ChangeMonitor, Handler
from gekko_sync.state import State
from gekko_sync.workspace import Workspace


class MonitorTests(unittest.TestCase):
    def test_debounce_keeps_busy_events_and_groups_duplicates(self):
        now = [0]
        monitor = ChangeMonitor(clock=lambda: now[0])
        monitor.record('id', ['file.txt'])
        now[0] = 1
        monitor.record('id', ['file.txt'])
        self.assertEqual(monitor.ready(), {})
        now[0] = 3
        self.assertEqual(monitor.ready(), {'id': {('file.txt',)}})
        self.assertEqual(monitor.ready(), {})

    def test_handler_ignores_internal_temp_delete_and_outside(self):
        folder = Path(tempfile.gettempdir()) / 'repo-test'
        monitor = Mock()
        handler = Handler(monitor, 'repo', folder)
        for parts, kind, directory in [(['.gekko-repository.json'], 'modified', False),
                (['.gekko-conflictos','a.txt'], 'created', False),
                (['~$document.docx'], 'created', False), (['file.txt'], 'deleted', False),
                (['file.part'], 'modified', False), (['folder'], 'modified', True),
                (['file.txt'], 'opened', False)]:
            handler.on_any_event(SimpleNamespace(src_path=str(folder.joinpath(*parts)), event_type=kind, is_directory=directory))
        handler.on_any_event(SimpleNamespace(src_path=str(folder.parent / 'private'), event_type='created', is_directory=False))
        monitor.record.assert_not_called()

    def test_atomic_save_uses_destination_name(self):
        folder = Path(tempfile.gettempdir()) / 'repo-test'
        monitor = Mock()
        handler = Handler(monitor, 'repo', folder)
        handler.on_any_event(SimpleNamespace(src_path=str(folder / '~$temp'), dest_path=str(folder / 'document.docx'),
                                            event_type='moved', is_directory=False))
        monitor.record.assert_called_once_with('repo', ('document.docx',))

    def test_only_registered_roots_are_scheduled_and_unchanged_not_restarted(self):
        observer = Mock()
        factory = Mock(return_value=observer)
        monitor = ChangeMonitor(observer_factory=factory)
        monitor.update({'repo': 'E:/aca/Juridico'})
        observer.schedule.assert_called_once()
        self.assertEqual(observer.schedule.call_args.args[1], 'E:/aca/Juridico')
        self.assertTrue(observer.schedule.call_args.kwargs['recursive'])
        monitor.update({'repo': 'E:/aca/Juridico'})
        factory.assert_called_once()
        monitor.record('repo', ['file'])
        monitor.stop()
        observer.stop.assert_called_once()
        self.assertEqual(monitor.pending, {})

    def test_native_observer_reports_real_file_creation_and_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            managed = root / 'managed'
            managed.mkdir()
            monitor = ChangeMonitor(quiet_seconds=0.05)
            try:
                monitor.update({'repo': managed})
                (root / 'ignored.txt').write_text('not watched')
                (managed / 'file.txt').write_text('hello')
                deadline, events = time.monotonic() + 5, {}
                while time.monotonic() < deadline and not events:
                    time.sleep(0.05)
                    events = monitor.ready()
                self.assertEqual(events, {'repo': {('file.txt',)}})
                (managed / 'file.txt').write_text('edited')
                deadline, events = time.monotonic() + 5, {}
                while time.monotonic() < deadline and not events:
                    time.sleep(0.05)
                    events = monitor.ready()
                self.assertEqual(events, {'repo': {('file.txt',)}})
            finally:
                monitor.stop()


class EventSyncTests(unittest.TestCase):
    def test_event_sync_does_not_upload_other_repository_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'docs'
            root.mkdir()
            state = State(base / 'state')
            workspace = Workspace(state, 'account')
            repositories = [{'_id':'one', 'nombre':'One'}, {'_id':'two', 'nombre':'Two'}]
            rows = workspace.configure(root, repositories)
            for row in rows:
                (Path(row['folder']) / 'file.txt').write_text('new')
            client = Mock()
            client.repositories.return_value = repositories
            with patch('gekko_sync.workspace.SyncEngine') as engine:
                workspace.sync(client, local_events={'one': {('file.txt',)}})
            self.assertEqual(engine.return_value.sync.call_count, 1)
            self.assertEqual(engine.return_value.sync.call_args.args[0]['repository'], 'one')

    def test_download_notifications_do_not_start_endless_sync_loop(self):
        from test_publisher import Server
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'docs'
            root.mkdir()
            state = State(base / 'state')
            workspace = Workspace(state, 'account')
            repositories = [{'_id':'one', 'nombre':'One'}]
            row = workspace.configure(root, repositories)[0]
            (Path(row['folder']) / 'file.txt').write_text('new')
            client = Server()
            client.repositories = lambda: repositories
            workspace.sync(client)
            with patch('gekko_sync.workspace.SyncEngine') as engine:
                workspace.sync(client, local_events={'one': {('file.txt',)}})
            engine.assert_not_called()
