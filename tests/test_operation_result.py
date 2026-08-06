from __future__ import annotations

import unittest

from core.services.operation_result import (
    OperationResult,
    OperationState,
)


class OperationResultTests(unittest.TestCase):
    def test_confirmed_rejected_and_unknown_have_distinct_semantics(self) -> None:
        confirmed = OperationResult.confirmed_result("ok")
        rejected = OperationResult.rejected_result("failed")
        unknown = OperationResult.unknown_result("pending")

        self.assertEqual(confirmed.state, OperationState.CONFIRMED)
        self.assertEqual(confirmed.as_legacy_tuple(), (True, "ok"))
        self.assertEqual(rejected.as_legacy_tuple(), (False, "failed"))
        self.assertEqual(unknown.as_legacy_tuple(), (True, "pending"))
        self.assertFalse(unknown.confirmed)

    def test_partial_and_rolled_back_are_explicit_failures(self) -> None:
        partial = OperationResult.partial_result("partly applied", step=2)
        rolled_back = OperationResult.rolled_back_result("restored")

        self.assertEqual(partial.state, OperationState.PARTIAL)
        self.assertEqual(partial.details, {"step": 2})
        self.assertEqual(partial.as_legacy_tuple(), (False, "partly applied"))
        self.assertEqual(rolled_back.state, OperationState.ROLLED_BACK)
        self.assertFalse(rolled_back.accepted)


if __name__ == "__main__":
    unittest.main()
