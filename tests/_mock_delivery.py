"""Legacy sender tests: isolate mocked Discord behavior from GitHub I/O.

Tests of the real remote GitHub Contents protocol belong in
test_durable_remote_delivery_gate.py. Existing tests of reaction scanning,
thumbnail gating, queue behavior, and local markers mock only the remote
reservation/settlement boundary, and can still assert legacy save_ids calls.
"""
from unittest.mock import Mock, patch


def patch_sender_delivery(testcase, *modules):
    for module in modules:
        # Legacy scan/queue tests mock the remote transport boundary. The
        # dedicated durable queue tests run against a simulated GitHub ledger.
        if hasattr(module, "restore_durable_queue"):
            recovery = patch.object(module, "restore_durable_queue")
            recovery.start()
            testcase.addCleanup(recovery.stop)
        if hasattr(module, "save_reaction_queue"):
            checkpoint = patch.object(module, "save_reaction_queue")
            checkpoint.start()
            testcase.addCleanup(checkpoint.stop)
        reserve = patch.object(module, "reserve_delivery", return_value="mocked-reservation")
        reserve.start()
        testcase.addCleanup(reserve.stop)

        def fake_confirm(kind, video_id, target=module):
            # Older tests mock save_ids to emulate permanent history. Keep
            # their original delivery assertions without making API calls.
            if isinstance(target.save_ids, Mock):
                return target.save_ids(kind, [video_id])
            return [video_id]

        confirmed = patch.object(module, "confirm_delivery", side_effect=fake_confirm)
        confirmed.start()
        testcase.addCleanup(confirmed.stop)

        rejected = patch.object(module, "reject_delivery", return_value=[])
        rejected.start()
        testcase.addCleanup(rejected.stop)
