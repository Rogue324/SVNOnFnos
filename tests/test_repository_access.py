import errno
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import server


class RepositoryAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.previous = self.root / 'old'
        self.previous.mkdir()
        store = Mock()
        store.root = self.data
        store.repos = self.previous
        store.lock = threading.RLock()
        store.backup_lock = threading.Lock()
        store.state = {'repositories': {'project': {}}}
        self.app = Mock()
        self.app.store = store
        self.app.settings_lock = threading.Lock()
        self.app.settings_file = self.root / 'settings.json'
        self.app.settings_file.write_text(json.dumps({'repos_path': str(self.previous)}))
        self.app.svn_host = ''
        self.app.auth_required = True
        self.app.svn_port = 3690
        self.app.maintenance = False
        self.app.stop_event = threading.Event()

    def save(self, destination):
        return server.save_settings(self.app, {'repositoriesPath': str(destination)})

    def assert_denied_without_migration(self, destination):
        before = self.app.settings_file.read_text()
        with self.assertRaises(server.Error) as raised:
            self.save(destination)
        self.assertEqual(403, raised.exception.status)
        self.assertIn('读写', str(raised.exception))
        self.assertIn('父目录', str(raised.exception))
        self.assertEqual(before, self.app.settings_file.read_text())
        self.assertEqual(self.previous, self.app.store.repos)
        self.app.svn_process.terminate.assert_not_called()

    def test_existing_and_new_directories_are_probed_and_left_private(self):
        existing = self.root / 'existing'
        existing.mkdir()
        sentinel = existing / 'keep'
        sentinel.write_text('unchanged')
        for directory in (existing, self.root / 'new' / 'nested'):
            self.assertEqual(directory, server.repository_directory(self.data, str(directory)))
            self.assertTrue(directory.is_dir())
            self.assertFalse(any(directory.glob('.fnsvn-access-*')))
        self.assertEqual('unchanged', sentinel.read_text())
        self.assertEqual(0, (self.root / 'new' / 'nested').stat().st_mode & 0o077)

    def test_existing_directory_requires_read_permission(self):
        destination = self.root / 'existing'
        destination.mkdir()
        with patch.object(server.os, 'scandir', side_effect=PermissionError(errno.EACCES, 'denied')):
            self.assert_denied_without_migration(destination)

    def test_new_directory_requires_parent_write_permission(self):
        destination = self.root / 'new' / 'nested'
        with patch.object(Path, 'mkdir', side_effect=PermissionError(errno.EACCES, 'denied')):
            self.assert_denied_without_migration(destination)
        self.assertFalse(destination.exists())

    def test_probe_file_must_be_readable_and_is_cleaned_up(self):
        destination = self.root / 'existing'
        destination.mkdir()
        original = Path.read_bytes

        def denied_probe(path):
            if path.name == 'write-test':
                raise PermissionError(errno.EACCES, 'denied')
            return original(path)

        with patch.object(Path, 'read_bytes', denied_probe):
            self.assert_denied_without_migration(destination)
        self.assertEqual([], list(destination.iterdir()))

    def test_existing_directory_requires_write_permission(self):
        destination = self.root / 'existing'
        destination.mkdir()
        with patch.object(server.tempfile, 'TemporaryDirectory', side_effect=PermissionError(errno.EACCES, 'denied')):
            self.assert_denied_without_migration(destination)

    def test_read_only_filesystem_reports_permissions(self):
        destination = self.root / 'existing'
        destination.mkdir()
        with patch.object(server.tempfile, 'TemporaryDirectory', side_effect=OSError(errno.EROFS, 'read-only')):
            self.assert_denied_without_migration(destination)

    def test_protected_directory_via_symlink_rejected(self):
        protected = self.data / 'config'
        protected.mkdir()
        link = self.root / 'link'
        link.symlink_to(protected, target_is_directory=True)
        with self.assertRaisesRegex(server.Error, '应用配置'):
            self.save(link)
        self.assertEqual([], list(protected.iterdir()))
        self.app.svn_process.terminate.assert_not_called()

    def test_existing_file_is_not_reported_as_permission_error(self):
        destination = self.root / 'file'
        destination.write_text('keep')
        with self.assertRaises(server.Error) as raised:
            self.save(destination / 'nested')
        self.assertEqual(400, raised.exception.status)
        self.assertIn('不是目录', str(raised.exception))
        self.assertEqual('keep', destination.read_text())

    def test_disk_full_is_not_misreported_as_permission_error(self):
        destination = self.root / 'existing'
        destination.mkdir()
        with patch.object(server.tempfile, 'TemporaryDirectory', side_effect=OSError(errno.ENOSPC, 'full')):
            with self.assertRaises(OSError) as raised:
                self.save(destination)
        self.assertEqual(errno.ENOSPC, raised.exception.errno)
        self.app.svn_process.terminate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
