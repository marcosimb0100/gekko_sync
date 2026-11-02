"""Invoked by build_windows.ps1; never includes the complete .env in a bundle."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))
from gekko_sync.configuration import read_env

parser = argparse.ArgumentParser()
parser.add_argument('--env-file', required=True)
parser.add_argument('--finalize', action='store_true')
args = parser.parse_args()
config = read_env(args.env_file)
build = PROJECT / 'build'
build.mkdir(exist_ok=True)
if args.finalize:
    bundled = json.loads((build / '_build_config.json').read_text(encoding='utf-8'))
    if config != bundled:
        raise SystemExit('El .env cambió durante la compilación. Genera nuevamente el instalador.')
    executable = PROJECT / 'dist' / 'GekkoSync-Setup-0.1.0-x64.exe'
    digest = hashlib.sha256()
    with executable.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    metadata = {**config, 'version': '0.1.0', 'filename': executable.name,
                'sha256': digest.hexdigest(), 'bytes': executable.stat().st_size}
    (PROJECT / 'dist' / 'GekkoSync-release.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
else:
    (build / '_build_config.json').write_text(json.dumps(config), encoding='utf-8')
