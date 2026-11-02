"""A container is never scanned: only registered repository children are synced."""
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from .engine import file_hash, is_link, safe_path
from .publisher import SyncEngine


class Workspace:
    def __init__(self, state, account):
        self.state, self.account = state, account
        self.key = account + ':workspace'

    def config(self):
        return self.state.get(self.key)

    def validate_root(self, folder):
        root = Path(folder).absolute()
        if not root.is_dir() or is_link(root):
            raise ValueError('Selecciona una carpeta principal existente, sin enlaces.')
        root = root.resolve()
        if root.parent == root or root.is_relative_to(self.state.directory.resolve()) or self.state.directory.resolve().is_relative_to(root):
            raise ValueError('La carpeta principal no puede ser la raíz del disco ni contener el estado del cliente.')
        for mapping in self.state.mappings():
            other = Path(mapping['folder']).resolve()
            legacy = mapping['account'] == self.account and other == root
            managed = mapping['account'] == self.account and self.config() and other.parent == root
            if not legacy and not managed and (root.is_relative_to(other) or other.is_relative_to(root)):
                raise ValueError('La carpeta se superpone con otra vinculación.')
        return root

    def configure(self, folder, repositories):
        root = self.validate_root(folder)
        config = self.config()
        if config and Path(config['root']) != root:
            raise ValueError('La carpeta principal ya está configurada. Su traslado requiere una migración independiente.')
        if not config:
            # Old mappings must either already be managed or be the single legacy root.
            if any(Path(row['folder']).resolve() != root for row in self.state.mappings(self.account)):
                raise ValueError('Selecciona como carpeta principal la carpeta de la vinculación anterior.')
            config = {'root': str(root), 'repositories': {}, 'migration': None}
            self.state.set(self.key, config)
        self.resume_migration(config)
        existing = {row['repository']: row for row in self.state.mappings(self.account)}
        # Migrate the legacy root before adding sibling repository mappings.
        ordered = sorted(repositories, key=lambda row: not (row['_id'] in existing and Path(existing[row['_id']]['folder']) == root))
        for repository in ordered:
            identifier = repository['_id']
            mapping = existing.get(identifier)
            if identifier in config['repositories']:
                if mapping and Path(mapping['folder']) == root:
                    target = self.validate_repository(identifier)
                    self.plan_migration(config, mapping, target)
                    self.resume_migration(config)
                elif not mapping:
                    target = self.validate_repository(identifier)
                    self.state.add_mapping(self.account, identifier, repository['nombre'], target)
                continue
            target = self.allocate(config, repository)
            if mapping:
                if Path(mapping['folder']) != root:
                    raise ValueError('Vinculación anterior incompatible; no se mezclará su contenido.')
                self.plan_migration(config, mapping, target)
                self.resume_migration(config)
            else:
                self.state.add_mapping(self.account, identifier, repository['nombre'], target)
        config['allowed'] = [row['_id'] for row in repositories]
        self.state.set(self.key, config)
        return self.state.mappings(self.account)

    def allocate(self, config, repository):
        root = Path(config['root'])
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', repository['nombre']).strip(' .')[:120] or 'Repositorio'
        try:
            safe_path(root, [name])
        except ValueError:
            name = 'Repositorio-' + repository['_id'][-8:]
        target = root / name
        # Never adopt a user's directory, even if its name matches a repository.
        while target.exists() or is_link(target) or any(Path(item['folder']) == target for item in config['repositories'].values()):
            target = root / (name + '-' + uuid4().hex[:8])
        token = uuid4().hex
        target.mkdir()
        marker = {'account': self.account, 'repository': repository['_id'], 'token': token}
        with (target / '.gekko-repository.json').open('x', encoding='utf-8') as output:
            json.dump(marker, output)
        config['repositories'][repository['_id']] = {'folder': str(target), 'token': token}
        self.state.set(self.key, config)
        return target

    def validate_repository(self, identifier):
        config = self.config()
        if not config or identifier not in config['repositories']:
            raise ValueError('Repositorio no administrado. Configura la carpeta principal.')
        root = Path(config['root'])
        self.validate_root(root)
        item = config['repositories'][identifier]
        folder = Path(item['folder'])
        if folder.parent != root or not folder.is_dir() or is_link(folder):
            raise ValueError('La carpeta del repositorio no está disponible o fue sustituida. No se leerá su contenido.')
        marker = folder / '.gekko-repository.json'
        if is_link(marker) or not marker.is_file() or marker.stat().st_size > 4096:
            raise ValueError('Falta la identificación de la carpeta administrada; no se sincronizará.')
        identity = json.loads(marker.read_text(encoding='utf-8'))
        if identity != {'account': self.account, 'repository': identifier, 'token': item['token']}:
            raise ValueError('La carpeta no corresponde al repositorio registrado.')
        return folder

    def plan_migration(self, config, mapping, target):
        source = Path(mapping['folder'])
        files, directories = [], []
        # Only paths known by the previous mapping are moved. Untracked root
        # content and untracked children remain untouched, outside the system.
        for row in self.state.entries(mapping['id']).values():
            path = safe_path(source, row['ruta'])
            if row['tipo'] == 'carpeta':
                directories.append(row['ruta'])
            elif path.is_file():
                files.append({'path': row['ruta'], 'hash': file_hash(path)})
        config['migration'] = {'mapping': mapping['id'], 'repository': mapping['repository'],
                               'source': str(source), 'target': str(target), 'files': files, 'directories': directories}
        self.state.set(self.key, config)

    def resume_migration(self, config):
        plan = config.get('migration')
        if not plan:
            return
        source, target = Path(plan['source']), self.validate_repository(plan['repository'])
        if target != Path(plan['target']) or source != Path(config['root']):
            raise ValueError('Plan de migración incompatible.')
        for parts in sorted(plan['directories'], key=len):
            safe_path(target, parts).mkdir(parents=True, exist_ok=True)
        for item in plan['files']:
            old, new = safe_path(source, item['path']), safe_path(target, item['path'])
            if old.exists():
                if new.exists() or file_hash(old) != item['hash']:
                    raise ValueError('Un archivo cambió durante el traslado; se conserva y se detiene la migración.')
                new.parent.mkdir(parents=True, exist_ok=True)
                os.rename(old, new)
            elif not new.is_file() or file_hash(new) != item['hash']:
                raise ValueError('No se pudo verificar un archivo trasladado. Se requiere revisión.')
        with self.state.connection() as db:
            db.execute('UPDATE mappings SET folder=? WHERE id=? AND account=?', (str(target), plan['mapping'], self.account))
        config['migration'] = None
        self.state.set(self.key, config)

    def has_local_changes(self, mapping, paths):
        """Ignore notifications produced by our own confirmed downloads."""
        known = {tuple(part.casefold() for part in row['ruta']): row for row in self.state.entries(mapping['id']).values()}
        root = Path(mapping['folder'])
        for parts in paths:
            path = safe_path(root, list(parts))
            if not path.exists():
                continue  # local deletions are intentionally not published
            row = known.get(tuple(part.casefold() for part in parts))
            if path.is_dir():
                if not row or row['tipo'] != 'carpeta':
                    return True
            elif path.is_file() and (not row or row.get('sha256') != file_hash(path)):
                return True
        return False

    def sync(self, client, report=lambda message: None, local_events=None):
        config = self.config()
        if not config:
            raise ValueError('Primero configura la carpeta principal.')
        self.resume_migration(config)
        repositories = client.repositories()  # server-authorized IDs, never local directory names
        self.configure(config['root'], repositories)
        allowed = {row['_id'] for row in repositories}
        for mapping in self.state.mappings(self.account):
            if mapping['repository'] not in allowed:
                report('Sin permiso Sync local; copia conservada: ' + mapping['name'])
                continue
            try:
                folder = self.validate_repository(mapping['repository'])
                if folder != Path(mapping['folder']):
                    raise ValueError('La vinculación no coincide con la carpeta administrada.')
                if local_events is not None:
                    paths = local_events.get(mapping['repository'])
                    if not paths or not self.has_local_changes(mapping, paths):
                        continue
                SyncEngine(client, self.state, report).sync(mapping)
            except Exception as error:
                report(mapping['name'] + ': ' + str(error))
                # Expired authentication or disabled API affects every repository.
                if getattr(error, 'status', None) in (401, 503):
                    raise
