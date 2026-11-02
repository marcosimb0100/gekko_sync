"""Receive-only synchronization. Local edits and server removals are preserved."""
import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_link(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def safe_path(root, parts):
    root = Path(root)
    if not root.is_dir() or is_link(root):
        raise ValueError("La carpeta local no está disponible o es un enlace. No se propagará ninguna eliminación.")
    if not isinstance(parts, list) or not parts:
        raise ValueError("Ruta remota inválida.")
    path = root
    reserved = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
    for part in parts:
        if (not isinstance(part, str) or not part or len(part) > 200 or part in {".", ".."}
                or part.casefold().startswith(".gekko-") or part.endswith((" ", "."))
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', part) or part.split(".")[0].upper() in reserved):
            raise ValueError("La API entregó un nombre incompatible o una ruta reservada.")
        path = path / part
        if is_link(path):
            raise ValueError("No se permite escribir a través de enlaces o uniones de carpetas.")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("La ruta está fuera de la carpeta configurada.")
    return path


def preserve(root, path, category):
    if not path.exists():
        return
    if not path.is_file() or is_link(path):
        raise ValueError("Hay una colisión con un directorio o enlace; resuélvela manualmente.")
    destination = Path(root) / category
    if is_link(destination):
        raise ValueError("La carpeta de recuperación no puede ser un enlace.")
    destination.mkdir(exist_ok=True)
    os.replace(path, destination / (uuid4().hex + "-" + path.name))


class ReceiveEngine:
    def __init__(self, client, state, report=lambda message: None):
        self.client, self.state, self.report = client, state, report

    def sync(self, mapping):
        identifier, changes = self.collect(mapping)
        self.apply(mapping, identifier, changes)

    def collect(self, mapping):
        root = Path(mapping["folder"])
        if not root.is_dir() or is_link(root):
            raise ValueError("La carpeta local no está disponible. No se publicaron eliminaciones.")
        manifest = self.client.json("POST", "/sync/windows/manifiestos", {"espacio": mapping["repository"]})
        identifier, cursor, changes = manifest["manifiesto"], 0, []
        while True:
            page = self.client.json("GET", f"/sync/windows/manifiestos/{identifier}?cursor={cursor}&limite=100")
            changes.extend(page["cambios"])
            next_cursor = page["cursor"]
            if page["fin"]:
                break
            if next_cursor <= cursor:
                raise ValueError("El manifiesto no avanzó. Se detuvo la descarga.")
            cursor = next_cursor
        return identifier, changes

    def apply(self, mapping, identifier, changes):
        root = Path(mapping['folder'])
        desired = self.state.entries(mapping["id"])
        for change in changes:
            if change["accion"] == "actualizar":
                continue
            if change["accion"] not in {"eliminado", "retirar_acceso"}:
                raise ValueError("Cambio de protocolo desconocido.")
            old = desired.pop(change["entrada"], None)
            if old and change["accion"] == "eliminado" and old["tipo"] == "archivo":
                preserve(root, safe_path(root, old["ruta"]), ".gekko-recuperados")
            # Revocation never removes a previously downloaded copy.
            self.state.remove_entry(mapping["id"], change["entrada"])
        previous = dict(desired)
        for change in changes:
            if change["accion"] == "actualizar":
                desired[change["entrada"]] = change["datos"]
        for entry, row in sorted(desired.items(), key=lambda item: (len(item[1]["ruta"]), item[1]["tipo"] != "carpeta")):
            path = safe_path(root, row["ruta"])
            if row["tipo"] == "carpeta":
                path.mkdir(parents=True, exist_ok=True)
            elif row["tipo"] == "archivo":
                self.receive_file(root, identifier, entry, row, previous.get(entry), path)
            else:
                raise ValueError("Tipo de recurso desconocido.")
            self.state.save_entry(mapping["id"], entry, row)
        self.client.json("POST", f"/sync/windows/manifiestos/{identifier}/confirmar", {})
        self.report("Recepción de cambios confirmada.")

    def receive_file(self, root, manifest, entry, row, previous, path):
        expected = row.get("sha256", "")
        if not re.fullmatch(r"[a-f0-9]{64}", expected) or type(row.get("bytes")) is not int or row["bytes"] < 0:
            raise ValueError("Metadatos de contenido inválidos.")
        old_path = safe_path(root, previous["ruta"]) if previous else None
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not path.is_file():
            raise ValueError(f"Existe una carpeta donde debe escribirse {path.name}.")
        if path.is_file() and file_hash(path) == expected:
            return
        if old_path and old_path != path and old_path.is_file() and not path.exists() and file_hash(old_path) == expected:
            os.replace(old_path, path)
            return
        self.report(f"Descargando: {row['nombre']}")
        temporary = path.parent / (".gekko-download-" + uuid4().hex)
        try:
            digest, size = hashlib.sha256(), 0
            with self.client.open("GET", f"/sync/windows/archivos/{entry}/contenido?manifiesto={manifest}") as source, temporary.open("xb") as target:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > row["bytes"]:
                        raise ValueError("La descarga supera el tamaño anunciado.")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if size != row["bytes"] or digest.hexdigest() != expected:
                raise ValueError("La descarga no pasó la verificación SHA-256; se conserva la copia anterior.")
            safe_path(root, row["ruta"])
            self.before_replace(root, row, previous, path)
            preserve(root, path, ".gekko-conflictos")
            if old_path and old_path != path:
                preserve(root, old_path, ".gekko-conflictos")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def before_replace(self, root, row, previous, path):
        """Extension point for bidirectional local-edit protection."""
