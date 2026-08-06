from __future__ import annotations

import unittest

from core.services.reachability import ReachabilityTracker


class ReachabilityTrackerTests(unittest.TestCase):
    def test_single_failure_after_success_is_suppressed(self) -> None:
        tracker = ReachabilityTracker(failure_threshold=3)
        tracker.update(
            router_id=1,
            probe_online=True,
            address="https://router.keenetic.pro:443",
        )

        result = tracker.update(
            router_id=1,
            probe_online=False,
            address="",
        )

        self.assertTrue(result.online)
        self.assertTrue(result.transient_failure_suppressed)
        self.assertEqual(
            result.address,
            "https://router.keenetic.pro:443",
        )

    def test_router_becomes_offline_after_threshold(self) -> None:
        tracker = ReachabilityTracker(failure_threshold=3)
        tracker.update(
            router_id=1,
            probe_online=True,
            address="http://192.168.1.1:80",
        )

        first = tracker.update(
            router_id=1,
            probe_online=False,
            address="",
        )
        second = tracker.update(
            router_id=1,
            probe_online=False,
            address="",
        )
        third = tracker.update(
            router_id=1,
            probe_online=False,
            address="",
        )

        self.assertTrue(first.online)
        self.assertTrue(second.online)
        self.assertFalse(third.online)
        self.assertEqual(third.consecutive_failures, 3)

    def test_seeded_database_online_state_suppresses_first_failure(self) -> None:
        tracker = ReachabilityTracker(failure_threshold=3)
        tracker.seed(
            router_id=5,
            online=True,
            address="https://saved.keenetic.pro",
        )
        result = tracker.update(
            router_id=5,
            probe_online=False,
            address="",
        )
        self.assertTrue(result.online)
        self.assertTrue(result.transient_failure_suppressed)
        self.assertEqual(result.consecutive_failures, 1)

    def test_success_resets_failure_counter(self) -> None:
        tracker = ReachabilityTracker(failure_threshold=3)
        tracker.update(router_id=1, probe_online=True, address="a")
        tracker.update(router_id=1, probe_online=False, address="")
        recovered = tracker.update(
            router_id=1,
            probe_online=True,
            address="b",
        )

        self.assertTrue(recovered.online)
        self.assertEqual(recovered.consecutive_failures, 0)
        self.assertEqual(recovered.address, "b")


if __name__ == "__main__":
    unittest.main()
