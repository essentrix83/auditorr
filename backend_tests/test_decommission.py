"""Safety and partial-failure coverage for the confirmation-only workflow."""

import json
import unittest
import urllib.parse
from unittest.mock import MagicMock, patch

import app
import arr


CONN = {
    'id': 'radarr-main', 'service': 'radarr',
    'base_url': 'http://radarr:7878', 'api_key': 'secret',
}
LIVE = {
    'id': 42, 'title': 'Exact Movie', 'tmdbId': 1234,
    'year': 2024, 'hasFile': False, 'monitored': True,
}


def _payload(**plan_overrides):
    plan = {
        'arr_action': 'keep_unmonitored',
        'add_import_exclusion': False,
        'delete_arr_files': False,
    }
    plan.update(plan_overrides)
    return {
        'confirmed': True,
        'target': {
            'service': 'radarr', 'connection_id': 'radarr-main', 'arr_id': 42,
            'tmdb_id': 1234, 'title': 'Exact Movie',
            'torrent': {'hash': 'AAAA', 'instance_id': 7},
        },
        'plan': plan,
    }


class DecommissionReportTests(unittest.TestCase):
    def test_report_only_surfaces_exact_import_pending_arr_targets(self):
        eligible = {
            'verdict': 'import_pending', 'hash': 'AAAA',
            'library': {'service': 'radarr', 'connection_id': 'radarr-main', 'arr_id': 42},
        }
        triage_items = [
            eligible,
            {'verdict': 'superseded', 'library': dict(eligible['library'])},
            {'verdict': 'import_pending', 'library': {'service': 'radarr', 'arr_id': 43}},
            {'verdict': 'import_pending', 'library': {'service': 'sonarr', 'connection_id': 'sonarr-main'}},
            {'verdict': 'import_pending', 'library': {'service': 'other', 'connection_id': 'x', 'arr_id': 1}},
            {'verdict': 'import_pending', 'library': None},
        ]
        cfg = {
            'TORRENT_SOURCE': 'qui', 'ALLOW_CLIENT_DELETE': True,
        }
        with patch.object(app, 'workflows_triage', side_effect=lambda: app.jsonify({
            'status': 'success', 'items': triage_items, 'truncated': False,
        })), patch.object(app, 'db_load_config', return_value=cfg):
            response = app.app.test_client().get('/api/workflows/decommission')

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['items'], [eligible])
        self.assertEqual(body['count'], 1)
        self.assertEqual(body['client_name'], 'qui')
        self.assertTrue(body['client_delete_allowed'])


class DecommissionEndpointTests(unittest.TestCase):
    def _base(self, cfg=None, live=None):
        cfg = cfg or {'ALLOW_CLIENT_DELETE': False}
        live = dict(LIVE if live is None else live)
        return (
            patch.object(app, 'db_load_config', return_value=cfg),
            patch.object(app, 'get_arr_item', return_value=(CONN, live)),
            patch.object(app, 'db_save_decommission_action'),
            patch.object(app, 'try_start_scanning', return_value=False),
        )

    def test_invalid_or_missing_target_is_rejected_before_lookup(self):
        with patch.object(app, 'get_arr_item') as get_item:
            response = app.app.test_client().post(
                '/api/workflows/decommission',
                json={'confirmed': True, 'target': {}, 'plan': {'arr_action': 'remove'}},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('item ID', response.get_json()['message'])
        get_item.assert_not_called()

    def test_exact_live_arr_identity_is_refetched_and_used(self):
        cfg_patch, get_patch, save_patch, scan_patch = self._base()
        with cfg_patch, get_patch as get_item, save_patch, scan_patch, \
             patch.object(app, 'set_arr_item_unmonitored') as unmonitor:
            response = app.app.test_client().post('/api/workflows/decommission', json=_payload())
        self.assertEqual(response.status_code, 200)
        get_item.assert_called_once_with(
            {'ALLOW_CLIENT_DELETE': False},
            'radarr', 'radarr-main', 42,
        )
        unmonitor.assert_called_once_with(CONN, 'radarr', LIVE)

    def test_changed_live_identity_is_rejected(self):
        changed = dict(LIVE, tmdbId=9999)
        cfg_patch, get_patch, save_patch, scan_patch = self._base(live=changed)
        with cfg_patch, get_patch, save_patch as save, scan_patch, \
             patch.object(app, 'set_arr_item_unmonitored') as unmonitor:
            response = app.app.test_client().post('/api/workflows/decommission', json=_payload())
        self.assertEqual(response.status_code, 409)
        self.assertIn('TMDb identity', response.get_json()['message'])
        unmonitor.assert_not_called()
        save.assert_not_called()

    def test_default_torrent_action_keeps_seeding(self):
        already_unmonitored = dict(LIVE, monitored=False)
        cfg_patch, get_patch, save_patch, scan_patch = self._base(live=already_unmonitored)
        with cfg_patch, get_patch, save_patch, scan_patch, \
             patch.object(app.sources, 'remove_torrents') as remove:
            response = app.app.test_client().post('/api/workflows/decommission', json=_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['components']['torrent']['status'], 'kept')
        remove.assert_not_called()

    def test_client_deletion_gate_rejects_before_arr_lookup(self):
        payload = _payload(torrent_action='remove_registration')
        with patch.object(app, 'db_load_config', return_value={'ALLOW_CLIENT_DELETE': False}), \
             patch.object(app, 'get_arr_item') as get_item, \
             patch.object(app.sources, 'remove_torrents') as remove:
            response = app.app.test_client().post('/api/workflows/decommission', json=payload)
        self.assertEqual(response.status_code, 403)
        get_item.assert_not_called()
        remove.assert_not_called()

    def test_safe_auto_torrent_deletion_uses_existing_partition(self):
        cfg = {'ALLOW_CLIENT_DELETE': True}
        already_unmonitored = dict(LIVE, monitored=False)
        cfg_patch, get_patch, save_patch, scan_patch = self._base(cfg=cfg, live=already_unmonitored)
        torrent_item = {'hash': 'AAAA', 'instance_id': 7}
        with cfg_patch, get_patch, save_patch, scan_patch, \
             patch.object(app, '_partition_removal_by_file_sharing',
                          return_value=([torrent_item], [])) as partition, \
             patch.object(app.sources, 'remove_torrents', return_value=1) as remove:
            response = app.app.test_client().post(
                '/api/workflows/decommission',
                json=_payload(torrent_action='remove_auto'),
            )
        self.assertEqual(response.status_code, 200)
        partition.assert_called_once_with(cfg, [torrent_item])
        remove.assert_called_once_with(cfg, [torrent_item], delete_files=True)
        torrent_result = response.get_json()['components']['torrent']
        self.assertEqual(torrent_result['files_deleted'], 1)
        self.assertEqual(torrent_result['files_kept'], 0)

    def test_arr_mutation_requests_fresh_audit_through_guard(self):
        cfg_patch, get_patch, save_patch, _ = self._base()
        fake_thread = MagicMock()
        with cfg_patch, get_patch, save_patch, \
             patch.object(app, 'set_arr_item_unmonitored'), \
             patch.object(app, 'try_start_scanning', return_value=True) as guard, \
             patch.object(app.threading, 'Thread', return_value=fake_thread) as thread:
            response = app.app.test_client().post('/api/workflows/decommission', json=_payload())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['audit_started'])
        guard.assert_called_once_with('decommission')
        thread.assert_called_once_with(
            target=app.run_audit_process, args=('decommission',), daemon=True)
        fake_thread.start.assert_called_once_with()

    def test_partial_component_failure_is_returned_and_recorded(self):
        cfg_patch, get_patch, save_patch, scan_patch = self._base()
        with cfg_patch, get_patch, save_patch as save, scan_patch, \
             patch.object(app, 'add_arr_import_exclusion',
                          side_effect=ValueError('exclusion rejected')), \
             patch.object(app, 'set_arr_item_unmonitored'):
            response = app.app.test_client().post(
                '/api/workflows/decommission',
                json=_payload(add_import_exclusion=True),
            )
        self.assertEqual(response.status_code, 207)
        body = response.get_json()
        self.assertEqual(body['failed_components'], ['arr_exclusion'])
        self.assertEqual(body['components']['arr_action']['status'], 'success')
        record = save.call_args[0][0]
        self.assertEqual(record['confirmed_plan']['add_import_exclusion'], True)
        self.assertEqual(record['live_arr_payload'], LIVE)
        self.assertEqual(record['result']['status'], 'partial')
        self.assertEqual(record['result']['components']['arr_exclusion']['status'], 'failed')


class ArrDecommissionRequestTests(unittest.TestCase):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b''

    def test_delete_parameters_are_service_specific_and_files_default_off(self):
        with patch.object(arr.urllib.request, 'urlopen', return_value=self._Response()) as open_mock:
            arr.delete_arr_item(CONN, 'radarr', LIVE)
        request = open_mock.call_args[0][0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        self.assertEqual(query, {'deleteFiles': ['false'], 'addImportExclusion': ['false']})

        sonarr_conn = dict(CONN, id='sonarr-main', service='sonarr')
        series = {'id': 8, 'title': 'Series', 'tvdbId': 55}
        with patch.object(arr.urllib.request, 'urlopen', return_value=self._Response()) as open_mock:
            arr.delete_arr_item(sonarr_conn, 'sonarr', series, delete_files=True)
        request = open_mock.call_args[0][0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        self.assertEqual(query, {'deleteFiles': ['true'], 'addImportListExclusion': ['false']})

    def test_import_exclusion_payload_uses_tmdb_for_radarr_tvdb_for_sonarr(self):
        with patch.object(arr.urllib.request, 'urlopen', return_value=self._Response()) as open_mock:
            arr.add_arr_import_exclusion(CONN, 'radarr', LIVE)
        request = open_mock.call_args[0][0]
        self.assertEqual(urllib.parse.urlparse(request.full_url).path, '/api/v3/exclusions')
        self.assertEqual(json.loads(request.data), {
            'tmdbId': 1234, 'movieTitle': 'Exact Movie', 'movieYear': 2024,
        })

        sonarr_conn = dict(CONN, id='sonarr-main', service='sonarr')
        series = {'id': 8, 'title': 'Series', 'tvdbId': 55}
        with patch.object(arr.urllib.request, 'urlopen', return_value=self._Response()) as open_mock:
            arr.add_arr_import_exclusion(sonarr_conn, 'sonarr', series)
        request = open_mock.call_args[0][0]
        self.assertEqual(urllib.parse.urlparse(request.full_url).path, '/api/v3/importlistexclusion')
        self.assertEqual(json.loads(request.data), {'tvdbId': 55, 'title': 'Series'})


if __name__ == '__main__':
    unittest.main()
