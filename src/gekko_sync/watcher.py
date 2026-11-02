"""Native filesystem events, debounced per managed repository, never the container."""
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


def relevant_parts(parts):
    return bool(parts) and not any(part.casefold().startswith('.gekko-') or part.startswith('~$') for part in parts) and not parts[-1].lower().endswith(('.crdownload', '.part'))


class Handler(FileSystemEventHandler):
    def __init__(self, monitor, repository, folder):
        self.monitor, self.repository, self.folder = monitor, repository, Path(folder)

    def on_any_event(self, event):
        if event.event_type not in {'created', 'modified', 'moved'}:
            return
        if event.is_directory and event.event_type == 'modified':
            return
        path = event.dest_path if event.event_type == 'moved' else event.src_path
        try:
            parts = Path(path).relative_to(self.folder).parts
        except ValueError:
            return
        if relevant_parts(parts):
            self.monitor.record(self.repository, parts)


class ChangeMonitor:
    def __init__(self, quiet_seconds=2, observer_factory=Observer, clock=time.monotonic):
        self.quiet_seconds, self.observer_factory, self.clock = quiet_seconds, observer_factory, clock
        self.observer = None
        self.targets = {}
        self.pending = {}
        self.lock = threading.Lock()

    def record(self, repository, parts):
        with self.lock:
            batch = self.pending.setdefault(repository, {'paths': set(), 'last': 0})
            batch['paths'].add(tuple(parts))
            batch['last'] = self.clock()

    def ready(self):
        with self.lock:
            now = self.clock()
            ready = {key: batch['paths'] for key, batch in self.pending.items() if now - batch['last'] >= self.quiet_seconds}
            for key in ready:
                del self.pending[key]
            return ready

    def update(self, targets):
        targets = {key: str(value) for key, value in targets.items()}
        if targets == self.targets:
            return
        self.stop()
        if not targets:
            return
        observer = self.observer_factory()
        try:
            for repository, folder in targets.items():
                observer.schedule(Handler(self, repository, folder), folder, recursive=True)
            observer.start()
        except Exception:
            observer.stop()
            if observer.is_alive():
                observer.join(timeout=2)
            raise
        self.targets, self.observer = targets, observer

    def stop(self):
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=2)
        self.observer = None
        self.targets = {}
        with self.lock:
            self.pending.clear()
