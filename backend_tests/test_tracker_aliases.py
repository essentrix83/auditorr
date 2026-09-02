import unittest

from sources import canonical_tracker_hosts


class TrackerAliasTests(unittest.TestCase):
    def test_equivalent_hosts_are_folded_once(self):
        cfg = {'TRACKER_HOST_ALIASES': {
            'tracker.tleechreload.org': 'tracker.torrentleech.org',
        }}
        self.assertEqual(canonical_tracker_hosts(cfg, [
            'tracker.torrentleech.org', 'tracker.tleechreload.org',
        ]), ['tracker.torrentleech.org'])

    def test_alias_chain_and_loop_do_not_duplicate_or_hang(self):
        cfg = {'TRACKER_HOST_ALIASES': {'old': 'middle', 'middle': 'new', 'a': 'b', 'b': 'a'}}
        self.assertEqual(canonical_tracker_hosts(cfg, ['old', 'new', 'a']), ['new', 'a'])


if __name__ == '__main__':
    unittest.main()
