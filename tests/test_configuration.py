import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gekko_sync.configuration import read_env, resolve_api_url


class ConfigurationTests(unittest.TestCase):
    def test_env_extracts_only_public_api_url(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('# comentario\nGEKKO_API_URL="https://client.test/api"\nSECRET_KEY=private\n')
            self.assertEqual(read_env(path), {'api_url': 'https://client.test/api'})

    def test_installer_url_overrides_old_sqlite_and_local_env(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / 'config.json'
            package.write_text(json.dumps({'api_url': 'https://new.test/api'}))
            state = Mock()
            state.get.return_value = 'https://old.test/api'
            self.assertEqual(resolve_api_url(state, frozen=True, package=package), ('https://new.test/api', True))
            state.get.assert_not_called()

    def test_packaged_client_without_configuration_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            resolve_api_url(Mock(), frozen=True, package=Path(folder) / 'missing.json')

    def test_remote_http_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('GEKKO_API_URL=http://remote.test/api')
            with self.assertRaises(ValueError):
                read_env(path)
