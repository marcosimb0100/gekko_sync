"""Current-user Windows startup and tray integration. No service or elevation."""
import subprocess
import sys
from pathlib import Path


class Startup:
    KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
    NAME = 'GekkoSync'

    def __init__(self, registry=None, executable=None, launcher=None):
        if registry is None:
            import winreg
            registry = winreg
        self.registry = registry
        executable = Path(executable or sys.executable).resolve()
        windowed = executable.with_name('pythonw.exe')
        self.executable = windowed if windowed.exists() else executable
        self.launcher = Path(launcher or Path(__file__).resolve().parents[2] / 'run.py').resolve()
        self.frozen = bool(getattr(sys, 'frozen', False))
        if self.frozen:
            self.executable = executable
        arguments = [str(self.executable)] if self.frozen else [str(self.executable), str(self.launcher)]
        self.command = subprocess.list2cmdline([*arguments, '--background'])

    def value(self):
        reg = self.registry
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, self.KEY, 0, reg.KEY_READ) as key:
                return reg.QueryValueEx(key, self.NAME)[0]
        except FileNotFoundError:
            return None

    def enabled(self):
        return self.value() == self.command

    def set_enabled(self, enabled):
        reg = self.registry
        if not enabled:
            value = self.value()
            if value is None:
                return
            if value != self.command:
                raise ValueError('La entrada de inicio pertenece a otra instalación de Gekko Sync.')
        elif not self.executable.is_file() or (not self.frozen and not self.launcher.is_file()):
            raise ValueError('No se encuentra el ejecutable o lanzador para iniciar con Windows.')
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, self.KEY, 0, reg.KEY_SET_VALUE) as key:
            if enabled:
                reg.SetValueEx(key, self.NAME, 0, reg.REG_SZ, self.command)
            else:
                reg.DeleteValue(key, self.NAME)


def create_icon():
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (64, 64))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=15, fill='#142d4e')
    draw.arc((14, 14, 50, 50), 40, 320, fill='#66d9cc', width=6)
    draw.line((35, 32, 49, 32, 49, 42), fill='#66d9cc', width=5)
    return image


class Tray:
    def __init__(self, dispatch):
        import pystray
        self.automatic = True
        def action(command):
            return lambda icon, item: dispatch(command)
        self.icon = pystray.Icon('GekkoSync', create_icon(), 'Gekko Sync', menu=pystray.Menu(
            pystray.MenuItem('Abrir Gekko Sync', action('show'), default=True),
            pystray.MenuItem('Sincronizar ahora', action('sync')),
            pystray.MenuItem('Sincronización automática', action('toggle'), checked=lambda item: self.automatic),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Salir', action('exit'))))

    def start(self):
        self.icon.run_detached()

    def update(self, automatic, busy=False):
        self.automatic = automatic
        self.icon.title = 'Gekko Sync · ' + ('Sincronizando' if busy else 'Automático' if automatic else 'En pausa')
        self.icon.update_menu()

    def stop(self):
        self.icon.stop()
