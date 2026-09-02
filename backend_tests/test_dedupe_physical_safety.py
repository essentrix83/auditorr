import types
import unittest
from unittest.mock import patch

from scripts import _build_dup_groups


def _record(path, file_id, duplicate_paths=None, linked_paths=None):
    return {
        'path': path,
        'inode': int(file_id.split(':')[-1]),
        'file_id': file_id,
        'size': 100,
        '_file_root': '/data',
        'duplicate_paths': duplicate_paths or [],
        'linked_paths': linked_paths or [],
        'excluded': False,
    }


class DedupePhysicalSafetyTests(unittest.TestCase):
    def test_same_backing_device_is_reclaimable(self):
        files = [_record('torrents/a.mkv', '10:1', ['/data/media/a.mkv'])]
        with patch('scripts._physical_stat', side_effect=[types.SimpleNamespace(st_dev=10), types.SimpleNamespace(st_dev=10)]):
            result = _build_dup_groups(files, '/data', '/data')
        group = result['groups'][0]
        self.assertEqual(group['status'], 'reclaimable_now')
        self.assertEqual(group['recoverable_size'], 100)

    def test_cross_device_is_report_only(self):
        files = [_record('torrents/a.mkv', '10:1', ['/data/media/a.mkv'])]
        with patch('scripts._physical_stat', side_effect=[types.SimpleNamespace(st_dev=10), types.SimpleNamespace(st_dev=11)]):
            result = _build_dup_groups(files, '/data', '/data')
        group = result['groups'][0]
        self.assertEqual(group['status'], 'cross_device')
        self.assertEqual(group['recoverable_size'], 0)
        self.assertTrue(group['skipped'])

    def test_unknown_physical_location_blocks_action(self):
        files = [_record('torrents/a.mkv', '10:1', ['/data/media/a.mkv'])]
        with patch('scripts._physical_stat', side_effect=[types.SimpleNamespace(st_dev=10), None]):
            result = _build_dup_groups(files, '/data', '/data')
        group = result['groups'][0]
        self.assertEqual(group['status'], 'blocked')
        self.assertEqual(group['recoverable_size'], 0)
        self.assertIn('could not be uniquely verified', group['blocker'])

    def test_reciprocal_alias_is_grouped_once(self):
        files = [
            _record('torrents/a.mkv', '10:1', ['/data/media/a.mkv'], ['/data/torrents/a-alias.mkv']),
            _record('torrents/a-alias.mkv', '10:1', ['/data/media/a.mkv']),
        ]
        with patch('scripts._physical_stat', side_effect=[types.SimpleNamespace(st_dev=10), types.SimpleNamespace(st_dev=10)]):
            result = _build_dup_groups(files, '/data', '/data')
        self.assertEqual(len(result['groups']), 1)


if __name__ == '__main__':
    unittest.main()
