"""Durable publication of new directories, new files and changed file contents."""
import hashlib
import os
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from .api import ApiError
from .engine import ReceiveEngine, file_hash, is_link, safe_path


def path_key(parts):
    return tuple(part.casefold() for part in parts)


class SyncEngine(ReceiveEngine):
    def sync(self, mapping):
        identifier, changes = self.collect(mapping)
        remote = self.state.entries(mapping['id'])
        for change in changes:
            if change['accion'] == 'actualizar':
                remote[change['entrada']] = change['datos']
            else:
                remote.pop(change['entrada'], None)
        publisher = Publisher(self.client, self.state, self.report)
        changed = publisher.publish(mapping, remote)
        self.conflicted_hashes = {path_key(job['path']): job['params'].get('sha256')
            for job in self.state.jobs(mapping['id']) if job['status'] in {'conflicto', 'rechazada', 'incierta'}}
        # A manifest captured before publication cannot describe our newly written revisions.
        if changed:
            identifier, changes = self.collect(mapping)
        self.apply(mapping, identifier, changes)
        self.report('Sincronización completada. Las eliminaciones locales no se envían al servidor.')

    def before_replace(self, root, row, previous, path):
        if not row.get('editar_local'):
            return
        candidates = [(path, row['ruta'])]
        if previous and previous['ruta'] != row['ruta']:
            candidates.append((safe_path(root, previous['ruta']), previous['ruta']))
        for candidate, parts in candidates:
            if not candidate.is_file():
                continue
            actual = file_hash(candidate)
            if actual in {row.get('sha256'), previous.get('sha256') if previous else None}:
                continue
            if actual == self.conflicted_hashes.get(path_key(parts)):
                continue
            raise ValueError('El archivo cambió durante la sincronización. Se conserva localmente y se publicará en el próximo ciclo.')


class Publisher:
    def __init__(self, client, state, report):
        self.client, self.state, self.report = client, state, report
        self.capabilities = {}
        self.changed = False

    def capability(self, mapping, parent):
        if parent not in self.capabilities:
            params = {'espacio': mapping['repository']}
            if parent:
                params['padre'] = parent
            self.capabilities[parent] = self.client.json('GET', '/sync/windows/capacidades?' + urlencode(params))
        return self.capabilities[parent]

    def writable(self, mapping, parent):
        try:
            actions = self.capability(mapping, parent)['acciones']
            return 'sync_local' in actions and 'editar_local' in actions
        except ApiError as error:
            if error.status in (403, 404):
                return False
            raise

    def spool(self, path, expected):
        directory = self.state.directory / 'outbox'
        directory.mkdir(exist_ok=True)
        target = directory / (uuid4().hex + '.bin')
        before = path.stat()
        digest = hashlib.sha256()
        try:
            with path.open('rb') as source, target.open('xb') as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or digest.hexdigest() != expected:
                raise ValueError('El archivo sigue cambiando; se intentará cuando termine de guardarse.')
            return target
        except BaseException:
            target.unlink(missing_ok=True)
            raise

    def submit(self, mapping, job, parent, remote):
        if job['status'] != 'pendiente':
            return None
        params, parts = job['params'], job['path']
        same_path = next((row for row in remote.values() if path_key(row['ruta']) == path_key(parts)), None)
        # Recover from a lost response without issuing a second creation.
        applied = same_path and ((params['accion'] == 'crear_carpeta' and same_path['tipo'] == 'carpeta') or (
            same_path['tipo'] == 'archivo' and same_path.get('sha256') == params.get('sha256') and (
                params['accion'] == 'crear_archivo' or same_path['id'] == params.get('entrada'))))
        if applied:
            row = {**same_path, 'ruta': parts}
            self.state.complete_job(job, row)
            self.changed = True
            self.report('Confirmado en servidor: ' + '/'.join(parts))
            return row
        if not self.writable(mapping, parent):
            self.report('Sin Editar local; pendiente conservado: ' + '/'.join(parts))
            return None
        try:
            if job['payload']:
                payload = Path(job['payload'])
                if not payload.is_file() or is_link(payload) or not payload.resolve().is_relative_to((self.state.directory / 'outbox').resolve()):
                    raise ValueError('No está disponible la copia pendiente de publicación.')
                if file_hash(payload) != params['sha256']:
                    raise ValueError('La copia pendiente cambió; se requiere revisión.')
                size = payload.stat().st_size
                if size > self.capability(mapping, parent)['limite_archivo_bytes']:
                    self.report('Excede el tamaño por archivo; se conserva pendiente: ' + '/'.join(parts))
                    return None
                self.report('Subiendo: ' + '/'.join(parts))
                result = self.client.upload_operation(params, payload, job['id'])
            else:
                size = 0
                self.report('Creando carpeta: ' + '/'.join(parts))
                result = self.client.operation_json(params, job['id'])
            row = {'id': result['entrada'], 'nombre': parts[-1], 'ruta': parts, 'padre': parent,
                'tipo': 'carpeta' if params['accion'] == 'crear_carpeta' else 'archivo',
                'revision': result['revision'], 'editar_local': True}
            if job['payload']:
                row.update(sha256=params['sha256'], bytes=size, version=result.get('version'))
            self.state.complete_job(job, row)
            self.changed = True
            return row
        except ApiError as error:
            if error.status in (0, 401, 403, 503) or error.status >= 500:
                self.state.job_error(job['id'], str(error))
                raise
            status = 'conflicto' if error.data.get('contenido_conservado') else 'rechazada'
            if error.status == 409 and ('pendiente' in str(error).lower() or 'interrumpida' in str(error).lower()):
                status = 'incierta'
            self.state.job_error(job['id'], str(error), status)
            self.report(f'{status.capitalize()}: {"/".join(parts)}. {error}')
            return None

    def publish(self, mapping, remote):
        root = Path(mapping['folder'])
        baseline = self.state.entries(mapping['id'])
        folders = {path_key(row['ruta']): row['id'] for row in [*baseline.values(), *remote.values()] if row['tipo'] == 'carpeta'}
        folders[()] = None
        for job in self.state.jobs(mapping['id'], 'pendiente'):
            parent_key = path_key(job['path'][:-1])
            if parent_key not in folders:
                continue
            row = self.submit(mapping, job, folders[parent_key], remote)
            if row:
                remote[row['id']] = row
                if row['tipo'] == 'carpeta':
                    folders[path_key(row['ruta'])] = row['id']
        baseline = self.state.entries(mapping['id'])
        known = {path_key(row['ruta']): row for row in baseline.values()}
        remote_paths = {path_key(row['ruta']): row for row in remote.values()}
        pending_paths = {path_key(job['path']) for job in self.state.jobs(mapping['id']) if job['status'] != 'completa'}

        def traversal_error(error):
            raise error

        for directory, children, filenames in os.walk(root, followlinks=False, onerror=traversal_error):
            relative = list(Path(directory).relative_to(root).parts)
            key = path_key(relative)
            if key not in folders or not self.writable(mapping, folders[key]):
                children[:] = [name for name in children if path_key(relative + [name]) in folders
                               and not is_link(Path(directory) / name)]
                if filenames:
                    self.report('Carpeta de solo recepción: ' + ('/'.join(relative) or mapping['name']))
                continue
            parent = folders[key]
            for name in list(sorted(children, key=str.casefold)):
                parts = relative + [name]
                child_key = path_key(parts)
                if name.casefold().startswith('.gekko-') or is_link(Path(directory) / name):
                    children.remove(name)
                    continue
                safe_path(root, parts)
                if child_key not in folders:
                    job = self.state.enqueue(mapping['id'], parts, {'accion': 'crear_carpeta', 'espacio': mapping['repository'],
                                                                  'padre': parent, 'nombre': name})
                    row = self.submit(mapping, job, parent, remote)
                    if row:
                        folders[child_key] = row['id']
                        remote[row['id']] = row
                    else:
                        children.remove(name)
            for name in sorted(filenames, key=str.casefold):
                if name.casefold().startswith('.gekko-') or name.startswith('~$') or name.endswith(('.crdownload', '.part')):
                    continue
                parts = relative + [name]
                key = path_key(parts)
                path = safe_path(root, parts)
                if is_link(path) or not path.is_file() or key in pending_paths:
                    continue
                if path.stat().st_size > self.capability(mapping, parent)['limite_archivo_bytes']:
                    self.report('Excede el máximo por archivo; se conserva localmente: ' + '/'.join(parts))
                    continue
                digest = file_hash(path)
                previous = known.get(key)
                if previous and previous.get('sha256') == digest:
                    continue
                current = remote_paths.get(key)
                if not previous and current and current.get('sha256') == digest:
                    continue
                params = {'accion': 'actualizar_archivo', 'entrada': previous['id'], 'revision': previous['revision']} if previous and previous['tipo'] == 'archivo' else {
                    'accion': 'crear_archivo', 'espacio': mapping['repository'], 'padre': parent, 'nombre': name}
                params['sha256'] = digest
                try:
                    payload = self.spool(path, digest)
                except ValueError as error:
                    self.report(f'{name}: {error}')
                    continue
                job = self.state.enqueue(mapping['id'], parts, params, payload)
                row = self.submit(mapping, job, parent, remote)
                if row:
                    remote[row['id']] = row
        return self.changed
