import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gekko_sync.desktop import Startup, Tray, create_icon


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name) / 'path with spaces'
        folder.mkdir()
        self.executable, self.launcher = folder / 'python.exe', folder / 'run.py'
        self.executable.touch()
        self.launcher.touch()
        self.registry = Mock()
        self.registry.OpenKey.side_effect = lambda *args: nullcontext('key')
        self.registry.CreateKeyEx.side_effect = lambda *args: nullcontext('key')
        self.startup = Startup(self.registry, self.executable, self.launcher)

    def test_startup_is_opt_in_and_current_user_only(self):
        self.registry.QueryValueEx.side_effect = FileNotFoundError
        self.assertFalse(self.startup.enabled())
        self.registry.SetValueEx.assert_not_called()
        self.startup.set_enabled(True)
        args = self.registry.SetValueEx.call_args.args
        self.assertEqual(args[1], 'GekkoSync')
        self.assertIn('--background', args[-1])
        self.assertIn('"', args[-1])
        self.assertEqual(self.registry.CreateKeyEx.call_args.args[0], self.registry.HKEY_CURRENT_USER)

    def test_disable_deletes_only_own_value_not_key(self):
        self.registry.QueryValueEx.return_value = (self.startup.command, 1)
        self.assertTrue(self.startup.enabled())
        self.startup.set_enabled(False)
        self.registry.DeleteValue.assert_called_once_with('key', 'GekkoSync')
        self.registry.DeleteKey.assert_not_called()

    def test_disable_does_not_remove_another_installation(self):
        self.registry.QueryValueEx.return_value = ('other command', 1)
        with self.assertRaises(ValueError):
            self.startup.set_enabled(False)
        self.registry.DeleteValue.assert_not_called()

    def test_missing_launcher_cannot_enable_startup(self):
        self.launcher.unlink()
        with self.assertRaises(ValueError):
            self.startup.set_enabled(True)
        self.registry.SetValueEx.assert_not_called()

    def test_packaged_executable_starts_without_python_or_run_py(self):
        self.launcher.unlink()
        with patch.object(sys, 'frozen', True, create=True):
            startup = Startup(self.registry, self.executable, self.launcher)
        self.assertNotIn('run.py', startup.command)
        self.assertIn('--background', startup.command)
        startup.set_enabled(True)
        self.registry.SetValueEx.assert_called_once()

    def test_tray_dispatches_without_touching_tk_from_worker(self):
        dispatch = Mock()
        with patch('pystray.Icon') as icon:
            tray = Tray(dispatch)
            menu = list(icon.call_args.kwargs['menu'].items)
            menu[0](None)
            dispatch.assert_called_once_with('show')
            tray.start()
            icon.return_value.run_detached.assert_called_once()
            tray.update(False, True)
            self.assertFalse(tray.automatic)
            self.assertIn('Sincronizando', icon.return_value.title)
            tray.stop()
            icon.return_value.stop.assert_called_once()

    def test_icon_is_readable_size_and_transparent(self):
        image = create_icon()
        self.assertEqual(image.size, (64, 64))
        self.assertEqual(image.mode, 'RGBA')


class DesktopUiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from gekko_sync.ui import Application
        from gekko_sync.state import State
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        with patch('gekko_sync.ui.Startup') as startup:
            startup.return_value.enabled.return_value = False
            self.app = Application(self.root, State(Path(self.temp.name)))
        self.app.tray = Mock()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.temp.cleanup()

    def test_hide_and_show_do_not_stop_synchronization(self):
        self.app.monitor = Mock()
        self.app.handle_hide()
        self.assertEqual(self.root.state(), 'withdrawn')
        self.app.monitor.stop.assert_not_called()
        self.app.handle_show()
        self.assertEqual(self.root.state(), 'normal')

    def test_close_hides_but_exit_cleans_up(self):
        tray = self.app.tray
        self.app.monitor = Mock()
        self.app.handle_close()
        tray.stop.assert_not_called()
        self.app.handle_exit()
        tray.stop.assert_called_once()
        self.app.monitor.stop.assert_called_once()

    def test_exit_waits_for_inflight_transfer(self):
        self.app.busy = True
        self.app.handle_exit()
        self.assertTrue(self.app.exit_requested)
        self.app.tray.stop.assert_not_called()
        self.app.events.put(('done', None))
        self.app.poll()
        self.assertIsNone(self.app.tray)

    def test_startup_error_restores_checkbox(self):
        self.app.start_with_windows.set(True)
        self.app.startup.set_enabled.side_effect = OSError('denied')
        with patch('gekko_sync.ui.messagebox.showerror') as error:
            self.app.handle_startup_toggle()
        self.assertFalse(self.app.start_with_windows.get())
        error.assert_called_once()
