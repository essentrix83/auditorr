import unittest
from unittest.mock import patch

import app
import state


class ScanControlTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        state.set_state(is_scanning=False, progress=0, total_files=0,
                        scanned_files=0, phase='idle', trigger='idle')

    def test_start_scan_reports_already_running_without_starting_thread(self):
        state.set_state(is_scanning=True, phase='disk', status_message='Scanning media directory...')
        with patch.object(app, 'try_start_scanning', return_value=False), \
             patch.object(app.threading, 'Thread') as thread:
            response = self.client.post('/api/start_scan')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['status'], 'already_running')
        self.assertTrue(body['scan']['is_scanning'])
        thread.assert_not_called()

    def test_start_scan_reports_started_only_after_claiming_scan(self):
        with patch.object(app, 'try_start_scanning', return_value=True), \
             patch.object(app.threading, 'Thread') as thread:
            response = self.client.post('/api/start_scan')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'started')
        thread.assert_called_once()

    def test_unknown_total_uses_indeterminate_progress(self):
        state.update_progress(123)
        current = state.get_state()
        self.assertEqual(current['scanned_files'], 123)
        self.assertEqual(current['total_files'], 0)
        self.assertIsNone(current['progress'])


if __name__ == '__main__':
    unittest.main()
