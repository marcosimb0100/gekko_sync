import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from . import vault


class State:
    def __init__(self, directory=None):
        self.directory = Path(directory or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GekkoSync")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.db"
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS secrets (key TEXT PRIMARY KEY, value BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS mappings (
                    id INTEGER PRIMARY KEY, account TEXT NOT NULL, repository TEXT NOT NULL,
                    name TEXT NOT NULL, folder TEXT NOT NULL, UNIQUE(account, repository));
                CREATE TABLE IF NOT EXISTS entries (
                    mapping INTEGER NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(mapping, id));
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, mapping INTEGER NOT NULL, path TEXT NOT NULL,
                    signature TEXT NOT NULL, params TEXT NOT NULL, payload TEXT,
                    status TEXT NOT NULL DEFAULT 'pendiente', error TEXT NOT NULL DEFAULT '',
                    UNIQUE(mapping,path,signature));
            """)
        if not self.get("installation"):
            self.set("installation", str(uuid4()))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))

    def secret(self, key):
        with self.connection() as db:
            row = db.execute("SELECT value FROM secrets WHERE key=?", (key,)).fetchone()
        return vault.unprotect(bytes(row[0])) if row else None

    def save_secret(self, key, value):
        encrypted = vault.protect(value)
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO secrets VALUES (?, ?)", (key, encrypted))

    def forget_secret(self, key):
        with self.connection() as db:
            db.execute("DELETE FROM secrets WHERE key=?", (key,))

    def mappings(self, account=None):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM mappings" + (" WHERE account=?" if account else ""),
                              (account,) if account else ()).fetchall()
        return [dict(row) for row in rows]

    def add_mapping(self, account, repository, name, folder):
        root = Path(folder).absolute()
        if not root.is_dir() or root.is_symlink() or getattr(root, "is_junction", lambda: False)():
            raise ValueError("Selecciona una carpeta local existente, sin enlaces ni uniones.")
        root = root.resolve()
        if root.parent == root or self.directory.resolve().is_relative_to(root) or root.is_relative_to(self.directory.resolve()):
            raise ValueError("La carpeta no puede contener el estado del cliente ni ser la raíz del disco.")
        for mapping in self.mappings():
            other = Path(mapping["folder"])
            if mapping["account"] == account and mapping["repository"] == repository:
                if other == root:
                    return
                raise ValueError("Este repositorio ya está vinculado a otra carpeta.")
            if root.is_relative_to(other) or other.is_relative_to(root):
                raise ValueError("La carpeta se superpone con otra vinculación, incluso de otra cuenta.")
        with self.connection() as db:
            db.execute("INSERT INTO mappings(account,repository,name,folder) VALUES (?,?,?,?)",
                       (account, repository, name, str(root)))

    def entries(self, mapping):
        with self.connection() as db:
            rows = db.execute("SELECT id,data FROM entries WHERE mapping=?", (mapping,)).fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

    def save_entry(self, mapping, identifier, data):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO entries VALUES (?,?,?)", (mapping, identifier, json.dumps(data)))

    def remove_entry(self, mapping, identifier):
        with self.connection() as db:
            db.execute("DELETE FROM entries WHERE mapping=? AND id=?", (mapping, identifier))

    def claim_instance(self):
        handle = (self.directory / 'instance.lock').open('a+b')
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError('Gekko Sync ya está abierto para este usuario de Windows.') from None
        return handle

    def jobs(self, mapping, status=None):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM outbox WHERE mapping=?' + (' AND status=?' if status else '') + ' ORDER BY rowid',
                              (mapping, status) if status else (mapping,)).fetchall()
        return [{**dict(row), 'path': json.loads(row['path']), 'params': json.loads(row['params'])} for row in rows]

    def enqueue(self, mapping, parts, params, payload=None):
        import hashlib
        signature = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        path = json.dumps(parts)
        identifier = str(uuid4())
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO outbox(id,mapping,path,signature,params,payload) VALUES (?,?,?,?,?,?)',
                       (identifier, mapping, path, signature, json.dumps(params), str(payload) if payload else None))
            row = db.execute('SELECT id FROM outbox WHERE mapping=? AND path=? AND signature=?',
                             (mapping, path, signature)).fetchone()
        if row[0] != identifier and payload:
            Path(payload).unlink(missing_ok=True)
        return next(item for item in self.jobs(mapping) if item['id'] == row[0])

    def complete_job(self, job, row):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO entries VALUES (?,?,?)', (job['mapping'], row['id'], json.dumps(row)))
            db.execute("UPDATE outbox SET status='completa', error='' WHERE id=?", (job['id'],))
        if job['payload']:
            try:
                Path(job['payload']).unlink(missing_ok=True)
            except OSError:
                pass

    def job_error(self, identifier, message, status='pendiente'):
        with self.connection() as db:
            db.execute('UPDATE outbox SET status=?,error=? WHERE id=?', (status, message, identifier))
