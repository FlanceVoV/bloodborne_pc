from paths import ROOT
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('bbport_lobby', ROOT / 'launcher/bbport_lobby.py')
lobby = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lobby)


def package(entry='dvdroot_ps4/chr/example.dcx'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as zip_file:
        zip_file.writestr('mod.json', json.dumps({'slug': 'sample-mod', 'version': '1.0'}))
        zip_file.writestr(entry, b'example')
    return out.getvalue()


class LobbyTests(unittest.TestCase):
    def test_catalog_uses_compatibility_profile(self):
        urls = []
        def open_request(request, timeout):
            urls.append(request.full_url)
            return io.BytesIO(b'[]')
        with patch.object(lobby.urllib.request, 'urlopen', side_effect=open_request):
            self.assertEqual(lobby.catalog('http://localhost:18080', 'supermedo-windows-v1.6'), ([], []))
        self.assertEqual(len(urls), 2)
        self.assertTrue(all('profile_id=supermedo-windows-v1.6' in url for url in urls))

    def test_install_verifies_archive_and_backs_up_previous_mod(self):
        data = package()
        meta = {'slug': 'sample-mod', 'version': '1.0', 'sha256': hashlib.sha256(data).hexdigest(),
                'download_url': '/api/v1/mods/1/download'}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / 'mods' / 'sample-mod'
            original.mkdir(parents=True)
            (original / 'old.txt').write_text('old')
            with patch.object(lobby.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
                installed = lobby.install_mod('http://localhost:18080', meta, root)
            self.assertEqual((installed / 'dvdroot_ps4/chr/example.dcx').read_bytes(), b'example')
            backups = list((root / 'mods-down/lobby-backups').glob('sample-mod-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / 'old.txt').read_text(), 'old')

    def test_install_rejects_unsafe_path_and_hash_mismatch(self):
        data = package('../outside.txt')
        meta = {'slug': 'sample-mod', 'version': '1.0', 'sha256': hashlib.sha256(data).hexdigest(),
                'download_url': '/api/v1/mods/1/download'}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(lobby.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(ValueError, 'Unsafe path'):
                    lobby.install_mod('http://localhost:18080', meta, root)
            meta['sha256'] = '0' * 64
            with patch.object(lobby.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                    lobby.install_mod('http://localhost:18080', meta, root)


if __name__ == '__main__':
    unittest.main()
