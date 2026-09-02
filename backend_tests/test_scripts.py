import os
import tempfile
import unittest

from scripts import _build_delete_script, _validate_delete_targets


class DeleteScriptSafetyTests(unittest.TestCase):
    def test_nested_target_is_validated_against_local_root(self):
        with tempfile.TemporaryDirectory() as root:
            nested = os.path.join(root, 'movies', 'release', 'movie.mkv')
            os.makedirs(os.path.dirname(nested))
            with open(nested, 'wb') as handle:
                handle.write(b'x')

            _validate_delete_targets([{'path': 'movies/release/movie.mkv'}], root)

    def test_stale_target_blocks_script_generation(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'no script was generated'):
                _validate_delete_targets([{'path': 'movies/release/missing.mkv'}], root)

    def test_generated_script_preflights_every_target_without_bc(self):
        script = _build_delete_script(
            [
                {'path': 'movies/one/movie.mkv', 'size': 10},
                {'path': 'movies/two/movie.mkv', 'size': 20},
            ],
            0,
            '2026-07-17 00:00:00',
            'Test Cleanup',
            'Test Cleanup',
            'test_cleanup.sh',
        )

        self.assertIn('TARGETS=(', script)
        self.assertIn('movies/one/movie.mkv', script)
        self.assertIn('movies/two/movie.mkv', script)
        self.assertIn('Cleanup targets changed since this script was generated.', script)
        self.assertNotIn('| bc)', script)


if __name__ == '__main__':
    unittest.main()
