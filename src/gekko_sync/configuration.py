"""A client-specific build overrides old local settings without reusing sessions."""
import json
import sys
from pathlib import Path

from .api import normalize_url


def read_env(path):
    values = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if separator and key.strip() == 'GEKKO_API_URL':
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key.strip()] = value
    if not values.get('GEKKO_API_URL'):
        raise ValueError('Configura GEKKO_API_URL en el archivo de compilación.')
    return {'api_url': normalize_url(values['GEKKO_API_URL'])}


def resolve_api_url(state, frozen=None, package=None, env_file=None):
    frozen = bool(getattr(sys, 'frozen', False)) if frozen is None else frozen
    package = Path(package or Path(__file__).parent / '_build_config.json')
    env_file = Path(env_file or Path(__file__).resolve().parents[2] / '.env')
    if frozen:
        if not package.is_file():
            raise ValueError('El instalador no incluye la dirección de su cliente. Genera nuevamente el instalador.')
        return normalize_url(json.loads(package.read_text(encoding='utf-8'))['api_url']), True
    if env_file.is_file():
        return read_env(env_file)['api_url'], True
    return normalize_url(state.get('api_url', 'http://localhost:7509/api')), False
