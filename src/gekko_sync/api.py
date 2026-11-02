import json
import ssl
from pathlib import Path
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler


class ApiError(RuntimeError):
    def __init__(self, message, status=0, data=None):
        super().__init__(message)
        self.status = status
        self.data = data if isinstance(data, dict) else {}


def normalize_url(value):
    url = value.strip().rstrip("/")
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Indica una URL de API sin credenciales, parámetros ni fragmentos.")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("Usa HTTPS. HTTP solo se admite para pruebas en localhost.")
    if parsed.path.rstrip("/") not in {"", "/api"}:
        raise ValueError("La dirección debe terminar en /api o indicar la raíz del servidor.")
    return url if parsed.path.rstrip("/") == "/api" else url + "/api"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ApiClient:
    def __init__(self, url, token=None, device=None, device_key=None):
        self.url = normalize_url(url)
        self.token, self.device, self.device_key = token, device, device_key
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def headers(self):
        headers = {"Accept": "application/json", "User-Agent": "GekkoSync/0.1"}
        if self.token:
            headers["Authorization"] = self.token if self.token.startswith("Bearer ") else "Bearer " + self.token
        if self.device and self.device_key:
            headers.update({"X-Sync-Device": self.device, "X-Sync-Key": self.device_key})
        return headers

    def open(self, method, path, data=None, extra_headers=None):
        headers = self.headers()
        headers.update(extra_headers or {})
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        if payload is not None:
            headers["Content-Type"] = "application/json"
        return self.perform(Request(self.url + path, data=payload, headers=headers, method=method))

    def perform(self, request):
        try:
            return self.opener.open(request, timeout=60)
        except HTTPError as error:
            details = {}
            try:
                body = json.loads(error.read(1048576))
                message = body.get("mensaje", "")
                details = body.get("datos", {})
            except (ValueError, UnicodeError):
                message = ""
            if error.code == 401:
                message = "La sesión venció o no es válida. Inicia sesión nuevamente."
            raise ApiError(message or f"La API respondió HTTP {error.code}.", error.code, details) from None
        except (URLError, TimeoutError, OSError):
            raise ApiError("No se pudo conectar a la API. Revisa la dirección, el certificado y la conexión.") from None

    def json(self, method, path, data=None):
        with self.open(method, path, data) as response:
            try:
                return json.load(response)["datos"]
            except (ValueError, KeyError, TypeError):
                raise ApiError("La respuesta no tiene el formato esperado de Gekko.") from None

    def login(self, email, password):
        data = self.json("POST", "/usuarios/acceso", {"correo_electronico": email, "clave": password})
        if not isinstance(data.get("token"), str) or not data["token"]:
            raise ApiError("La API no entregó una sesión válida.")
        self.token = data["token"]
        return data

    def operation_json(self, params, key):
        with self.open('POST', '/sync/windows/operaciones', params, {'Idempotency-Key': key}) as response:
            return json.load(response)['datos']

    def upload_operation(self, params, filename, key):
        path = Path(filename)
        boundary = 'Gekko-' + uuid4().hex
        prefix = b''
        for field, value in params.items():
            if value is not None:
                prefix += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n').encode('utf-8')
        prefix += (f'--{boundary}\r\nContent-Disposition: form-data; name="archivo"; filename="upload.bin"\r\n'
                   'Content-Type: application/octet-stream\r\n\r\n').encode('ascii')
        suffix = f'\r\n--{boundary}--\r\n'.encode('ascii')
        size = path.stat().st_size

        def chunks():
            yield prefix
            with path.open('rb') as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    yield chunk
            yield suffix

        headers = {**self.headers(), 'Content-Type': f'multipart/form-data; boundary={boundary}',
                   'Content-Length': str(len(prefix) + size + len(suffix)), 'Idempotency-Key': key}
        with self.perform(Request(self.url + '/sync/windows/operaciones', data=chunks(), headers=headers, method='POST')) as response:
            return json.load(response)['datos']

    def repositories(self):
        rows, offset = [], 0
        while True:
            batch = self.json("GET", f"/sync/espacios?limite=100&offset={offset}")["espacios"]
            rows.extend(item for item in batch if "sync_local" in item.get("acciones", []))
            if len(batch) < 100:
                return rows
            offset += 100

    def device_status(self, identifier):
        offset = 0
        while True:
            batch = self.json("GET", f"/sync/windows/dispositivos?offset={offset}")["equipos"]
            for item in batch:
                if item["_id"] == identifier:
                    return item["estado"]
            if len(batch) < 100:
                raise ApiError("El equipo no está registrado para esta cuenta.")
            offset += 100
