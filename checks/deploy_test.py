"""Archive boundaries are tested without network or server writes."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import json
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deploy', 'deploy/reshow-deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def archive(extra=()):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as bundle:
        for name, content, kind in [('repo/index.html', b'home', tarfile.REGTYPE), ('repo/js/app.js', b'code', tarfile.REGTYPE), *extra]:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
    return data.getvalue()


class DownloadTests(unittest.TestCase):
    def test_download_uses_team_repository_and_exact_commit(self):
        commit = '5c951b863b0b9cc57e084a175b096171488068bc'
        content = archive()
        with patch.object(deploy.urllib.request, 'urlopen') as fetch:
            response = fetch.return_value.__enter__.return_value
            response.read.return_value = content
            self.assertEqual(deploy.download_archive(commit), content)
            fetch.assert_called_once_with('https://codeload.github.com/ee3070-team/reshow/tar.gz/' + commit, timeout=60)
            response.read.assert_called_once_with(deploy.MAX_ARCHIVE + 1)

    def test_invalid_commit_is_rejected_before_network_access(self):
        with patch.object(deploy.urllib.request, 'urlopen') as fetch:
            for commit in ('', 'a' * 39, 'a' * 41, 'A' * 40, 'g' * 40, 'a' * 40 + '\n', '../master', 'a' * 40 + '?repo=other'):
                with self.subTest(commit=commit), self.assertRaisesRegex(ValueError, '40-character commit SHA'):
                    deploy.download_archive(commit)
            fetch.assert_not_called()

    def test_oversized_download_is_rejected(self):
        with patch.object(deploy, 'MAX_ARCHIVE', 8), patch.object(deploy.urllib.request, 'urlopen') as fetch:
            response = fetch.return_value.__enter__.return_value
            response.read.return_value = b'x' * 9
            with self.assertRaisesRegex(ValueError, 'Archive exceeds size limit'):
                deploy.download_archive('a' * 40)
            response.read.assert_called_once_with(9)


class ArchiveTests(unittest.TestCase):
    def test_only_runtime_is_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            deploy.extract_runtime(archive([('repo/.env', b'secret', tarfile.REGTYPE), ('repo/assets/model.glb', b'glTF', tarfile.REGTYPE), ('repo/auth/callback.html', b'callback', tarfile.REGTYPE), ('repo/auth/private.html', b'not published', tarfile.REGTYPE), ('repo/auth/private.py', b'not published', tarfile.REGTYPE)]), destination)
            self.assertEqual(sorted(str(p.relative_to(destination)) for p in destination.rglob('*') if p.is_file()), ['assets/model.glb', 'auth/callback.html', 'index.html', 'js/app.js'])

    def test_unsupported_runtime_file_reports_path_and_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, r"repo/assets/secret\.json.*file type is not allowed"):
                deploy.extract_runtime(archive([('repo/assets/secret.json', b'{}', tarfile.REGTYPE)]), Path(temporary))

    def test_current_glb_asset_archive_is_accepted(self):
        asset = Path('assets/another_pot.glb')
        if not asset.exists():
            self.skipTest('Current GLB asset is not present in this checkout')
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as bundle:
            for name in ('index.html', 'js/app.js', str(asset)):
                content = Path(name).read_bytes()
                member = tarfile.TarInfo(f'reshow/{name}')
                member.size = len(content)
                bundle.addfile(member, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            deploy.extract_runtime(data.getvalue(), destination)
            self.assertEqual((destination / asset).stat().st_size, asset.stat().st_size)

    def test_runtime_traversal_symlinks_and_duplicate_are_rejected(self):
        for name, kind in [('repo/js/../../escape.js', tarfile.REGTYPE), ('repo/js/link.js', tarfile.SYMTYPE), ('repo/index.html', tarfile.REGTYPE), ('/repo/js/absolute.js', tarfile.REGTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(ValueError):
                    deploy.extract_runtime(archive([(name, b'', kind)]), Path(temporary))


class LegacyAdoptionTests(unittest.TestCase):
    def setup_release(self, root, commit):
        release = root / 'releases' / commit
        public = release / 'public'
        (public / 'js').mkdir(parents=True)
        (public / 'index.html').write_bytes(b'home')
        (public / 'js/app.js').write_bytes(b'code')
        (root / 'current').symlink_to(release)
        return public

    def test_adoption_requires_exact_installed_hashes_without_switch_or_restart(self):
        commit = 'a' * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = self.setup_release(root, commit)
            with patch.object(deploy, 'ROOT', root), patch.object(deploy, 'download_archive', return_value=archive()), patch.object(deploy, 'point_current') as switch, patch.object(deploy.subprocess, 'run') as restart:
                deploy.adopt(commit)
                self.assertEqual(json.loads((public / 'release.json').read_text()), {'commit': commit})
                switch.assert_not_called()
                restart.assert_not_called()

    def test_normal_stamped_release_is_no_op_but_mismatched_stamp_fails(self):
        commit = 'b' * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = self.setup_release(root, commit)
            stamp = public / 'release.json'
            stamp.write_text(json.dumps({'commit': commit}))
            with patch.object(deploy, 'ROOT', root), patch.object(deploy, 'download_archive') as download:
                deploy.adopt('a' * 40)
                download.assert_not_called()
                stamp.write_text(json.dumps({'commit': 'c' * 40}))
                with self.assertRaisesRegex(ValueError, 'stamp differs'):
                    deploy.adopt('a' * 40)

    def test_adoption_rejects_wrong_release_changed_or_extra_files_and_symlinks(self):
        commit = 'a' * 40
        for kind in ('wrong-release', 'changed', 'extra', 'symlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                public = self.setup_release(root, 'b' * 40 if kind == 'wrong-release' else commit)
                if kind == 'changed':
                    (public / 'js/app.js').write_bytes(b'changed')
                if kind == 'extra':
                    (public / 'private.txt').write_text('not allowed')
                if kind == 'symlink':
                    (public / 'link.js').symlink_to(public / 'js/app.js')
                with patch.object(deploy, 'ROOT', root), patch.object(deploy, 'download_archive', return_value=archive()):
                    with self.assertRaises(ValueError):
                        deploy.adopt(commit)
                self.assertFalse((public / 'release.json').exists())

    def test_adoption_rejects_current_outside_the_managed_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            foreign = root / ('a' * 40)
            foreign.mkdir()
            (root / 'current').symlink_to(foreign)
            with patch.object(deploy, 'ROOT', root), patch.object(deploy, 'download_archive') as download:
                with self.assertRaisesRegex(ValueError, 'outside the managed'):
                    deploy.adopt('a' * 40)
                download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
