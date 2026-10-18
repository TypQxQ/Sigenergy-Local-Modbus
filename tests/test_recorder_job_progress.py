"""Actual executor queue/query phases and safe diagnostic snapshots."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_pv_restore import pv, NOW
from test_remote_ems_write_readback import _make_coordinator


class TestRecorderJobProgress(unittest.TestCase):
    def test_actual_worker_reports_queue_query_and_final_measurements(self):
        for error in (None, OSError("private database password")):
            with self.subTest(failed=error is not None):
                job = pv.RecorderJobProgress(10)
                blocker_started, unblock, query_started, finish = Event(), Event(), Event(), Event()
                coordinator, _hub, namespace = _make_coordinator()
                clock = [12]
                namespace["time"] = SimpleNamespace(monotonic=lambda: clock[0])
                coordinator.pv_restore_states["sensor.pv"] = {
                    "status": "waiting_for_recorder", "_recorder_job": job,
                }

                def blocker():
                    blocker_started.set()
                    if not unblock.wait(2):
                        raise RuntimeError("test queue was not released")

                def query(*args):
                    query_started.set()
                    if not finish.wait(2):
                        raise RuntimeError("test query was not released")
                    if error is not None:
                        raise error
                    return pv.RecorderRestore(None, False)

                with ThreadPoolExecutor(max_workers=1) as executor, \
                     patch.object(pv, "monotonic", side_effect=[15, 18]), \
                     patch.object(pv, "_read_recorder_restore", side_effect=query):
                    executor.submit(blocker)
                    self.assertTrue(blocker_started.wait(2))
                    future = executor.submit(
                        pv.read_recorder_restore, object(), "sensor.pv", NOW, None, 10, job,
                    )
                    try:
                        row = coordinator.pv_restore_diagnostics()["sensor.pv"]
                        self.assertEqual("queued", row["recorder_job_phase"])
                        self.assertEqual(2, row["recorder_queue_elapsed"])
                        self.assertIsNone(row["recorder_queue_wait"])
                        unblock.set()
                        self.assertTrue(query_started.wait(2))
                        clock[0] = 16
                        row = coordinator.pv_restore_diagnostics()["sensor.pv"]
                        self.assertEqual("querying", row["recorder_job_phase"])
                        self.assertEqual(5, row["recorder_queue_wait"])
                        self.assertEqual(1, row["recorder_query_elapsed"])
                        self.assertIsNone(row["recorder_query_time"])
                    finally:
                        unblock.set()
                        finish.set()
                    if error is not None:
                        with self.assertRaises(OSError) as caught:
                            future.result(timeout=2)
                        self.assertIs(error, caught.exception)
                    else:
                        result = future.result(timeout=2)
                        self.assertEqual(5, result.queue_wait)
                        self.assertEqual(3, result.query_time)
                clock[0] = 100
                row = coordinator.pv_restore_diagnostics()["sensor.pv"]
                self.assertEqual("failed" if error else "complete", row["recorder_job_phase"])
                self.assertEqual(5, row["recorder_queue_wait"])
                self.assertEqual(3, row["recorder_query_time"])
                self.assertEqual(3, row["recorder_query_elapsed"])
                self.assertEqual("OSError" if error else None, row["recorder_job_error_type"])
                self.assertNotIn("private", json.dumps(row))

    def test_cancelled_future_does_not_claim_a_running_worker_has_stopped(self):
        queued = pv.RecorderJobProgress(10)
        queued.future_cancelled(12)
        row = queued.diagnostics(100)
        self.assertEqual("cancelled", row["recorder_job_phase"])
        self.assertEqual(2, row["recorder_queue_elapsed"])
        self.assertTrue(row["recorder_future_cancelled"])
        # Cancelling an asyncio Future does not terminate an executor thread.
        queued.started(15)
        self.assertEqual("querying", queued.diagnostics(16)["recorder_job_phase"])
        queued.finished(18)
        row = queued.diagnostics(100)
        self.assertEqual("complete", row["recorder_job_phase"])
        self.assertEqual(3, row["recorder_query_time"])
        self.assertTrue(row["recorder_future_cancelled"])

    def test_old_worker_and_returned_metadata_cannot_modify_replacement_status(self):
        old = pv.RecorderJobProgress(10)
        details = {"reasons": ["unknown_state"], "latest_updated": NOW.isoformat(),
                   "cutoff": NOW.isoformat()}
        old.rejected_latest(details)
        details["reasons"].clear()
        snapshot = old.diagnostics(12)
        self.assertEqual(["unknown_state"], snapshot["recorder_latest_state_rejection"]["reasons"])
        snapshot["recorder_latest_state_rejection"]["reasons"].clear()
        self.assertEqual(["unknown_state"], old.diagnostics(12)["recorder_latest_state_rejection"]["reasons"])
        coordinator, _hub, _namespace = _make_coordinator()
        replacement = pv.RecorderJobProgress(20)
        coordinator.pv_restore_states["sensor.pv"] = {"_recorder_job": replacement}
        old.started(15)
        old.finished(18, "OSError")
        row = coordinator.pv_restore_diagnostics()["sensor.pv"]
        self.assertEqual("queued", row["recorder_job_phase"])
        self.assertIsNone(row["recorder_job_error_type"])
        self.assertIsNone(row["recorder_latest_state_rejection"])
        json.dumps(row)


if __name__ == "__main__":
    unittest.main()
