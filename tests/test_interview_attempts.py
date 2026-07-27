from __future__ import annotations

import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from openlearn import interview_attempts


class AttemptStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "learning-topics"
        self.workspace = self.root / "drills" / "algorithms" / "two-sum.py"
        self.workspace.parent.mkdir(parents=True)
        self.workspace.write_text("def two_sum(values, target):\n    pass\n", encoding="utf-8")
        self.generation = "topic_" + "1" * 32
        self.generations: dict[str, str | None] = {"algorithms": self.generation}
        self.locks: dict[str, threading.RLock] = {}
        self.store = self.make_store()
        self.problem = {
            "catalog_id": "openlearn-interview",
            "catalog_revision": 7,
            "problem_id": "two-sum-original",
            "problem_revision": 2,
            "problem_checksum": "a" * 64,
        }

    def make_store(self, writer=None) -> interview_attempts.AttemptStore:
        @contextmanager
        def lock(path: Path):
            value = self.locks.setdefault(str(path), threading.RLock())
            with value:
                yield

        def atomic_write(path: Path, text: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)

        return interview_attempts.AttemptStore(
            self.root,
            lock,
            writer or atomic_write,
            self.generations.get,
        )

    def create(self, **overrides):
        values = {
            "topic": "algorithms",
            "topic_generation": self.generation,
            "problem": self.problem,
            "workspace": self.workspace,
            "language": "python",
            "activity_id": "act_" + "2" * 32,
            "purpose": "practice",
            "profile_ref": "algorithms.interview.json",
            "clarification": "Can values repeat?",
            "plan": "Track the first index for each complement.",
        }
        values.update(overrides)
        return self.store.create(**values)

    def test_attempt_survives_restart_with_exact_revision_and_relative_workspace(self) -> None:
        created = self.create()
        restarted = self.make_store()
        loaded = restarted.load("algorithms", str(created["attempt_id"]))

        self.assertEqual(loaded["problem"], self.problem)
        self.assertEqual(loaded["profile_ref"], "algorithms.interview.json")
        self.assertEqual(loaded["workspace_ref"], "drills/algorithms/two-sum.py")
        self.assertEqual(restarted.resolve_workspace(loaded), self.workspace.resolve())
        self.assertFalse(Path(str(loaded["workspace_ref"])).is_absolute())

    def test_snapshot_is_bounded_hashed_and_idempotent(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])

        first = self.store.snapshot("algorithms", attempt_id)
        second = self.store.snapshot("algorithms", attempt_id)

        self.assertEqual(len(first["snapshots"]), 1)
        self.assertEqual(second["revision"], first["revision"])
        snapshot = first["snapshots"][0]
        self.assertEqual(snapshot["size_bytes"], self.workspace.stat().st_size)
        self.assertEqual(len(snapshot["sha256"]), 64)
        self.assertIn("def two_sum", snapshot["content"])

    def test_test_replay_is_idempotent_and_runner_failure_is_not_learner_failure(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        _record, run_id = self.store.start_test("algorithms", attempt_id)

        failed = self.store.finish_test(
            "algorithms",
            attempt_id,
            run_id,
            outcome="runner_error",
            output="container runtime stopped",
        )
        replayed = self.store.finish_test(
            "algorithms",
            attempt_id,
            run_id,
            outcome="runner_error",
            output="container runtime stopped",
        )

        self.assertEqual(len(replayed["test_runs"]), 1)
        self.assertEqual(replayed["revision"], failed["revision"])
        self.assertFalse(replayed["test_runs"][0]["learner_failure"])
        events = list(
            interview_attempts.iter_events(
                self.store.events_path("algorithms", attempt_id)
            )
        )
        finished = [event for event in events if event["event_type"] == "attempt_test_finished"]
        self.assertEqual(len(finished), 1)

    def test_interrupted_test_is_distinct_and_can_be_retried(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        _record, first_run = self.store.start_test("algorithms", attempt_id)
        interrupted = self.store.finish_test(
            "algorithms", attempt_id, first_run, outcome="interrupted"
        )
        _record, retry_run = self.store.start_test("algorithms", attempt_id)
        passed = self.store.finish_test(
            "algorithms", attempt_id, retry_run, outcome="passed"
        )

        self.assertFalse(interrupted["test_runs"][0]["learner_failure"])
        self.assertNotEqual(first_run, retry_run)
        self.assertEqual([run["outcome"] for run in passed["test_runs"]], ["interrupted", "passed"])

    def test_completed_attempt_is_immutable_except_idempotent_additive_evidence(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        completed = self.store.complete("algorithms", attempt_id)

        with self.assertRaisesRegex(interview_attempts.AttemptError, "immutable"):
            self.store.record_assistance("algorithms", attempt_id, hint="Use a map.")
        evidenced = self.store.add_evidence(
            "algorithms", attempt_id, "evidence_review_1", kind="review"
        )
        replayed = self.store.add_evidence(
            "algorithms", attempt_id, "evidence_review_1", kind="review"
        )

        self.assertEqual(completed["disposition"], "solved_independently")
        self.assertEqual(len(evidenced["evidence_refs"]), 1)
        self.assertEqual(replayed["revision"], evidenced["revision"])

    def test_full_solution_exposure_forces_assisted_disposition(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        self.store.record_assistance(
            "algorithms", attempt_id, full_solution_exposed=True
        )
        completed = self.store.complete(
            "algorithms", attempt_id, disposition="solved_independently"
        )
        self.assertEqual(completed["disposition"], "solved_with_help")

    def test_abandonment_and_cancellation_are_distinct_and_preserve_workspace(self) -> None:
        abandoned = self.create()
        cancelled = self.create()

        abandoned = self.store.abandon(
            "algorithms", str(abandoned["attempt_id"]), "moved on"
        )
        cancelled = self.store.cancel(
            "algorithms", str(cancelled["attempt_id"]), "declined before work"
        )

        self.assertEqual(abandoned["status"], "abandoned")
        self.assertIsNotNone(abandoned["abandoned_at"])
        self.assertIsNone(abandoned["cancelled_at"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(cancelled["cancelled_at"])
        self.assertTrue(self.workspace.exists())

    def test_explicit_retry_creates_new_attempt_and_default_create_never_resumes(self) -> None:
        first = self.create()
        second = self.create()
        retried = self.store.retry("algorithms", str(first["attempt_id"]))

        self.assertEqual(len({first["attempt_id"], second["attempt_id"], retried["attempt_id"]}), 3)
        self.assertEqual(retried["problem"], first["problem"])
        self.assertEqual(retried["status"], "active")
        self.assertNotEqual(retried["workspace_ref"], first["workspace_ref"])
        self.assertEqual(
            self.store.resolve_workspace(retried).read_text(encoding="utf-8"),
            self.workspace.read_text(encoding="utf-8"),
        )

    def test_concurrent_mutations_have_no_lost_or_duplicate_test_runs(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])

        def start(_index: int) -> str:
            _record, run_id = self.store.start_test("algorithms", attempt_id)
            return run_id

        with ThreadPoolExecutor(max_workers=8) as executor:
            run_ids = list(executor.map(start, range(24)))

        loaded = self.store.load("algorithms", attempt_id)
        self.assertEqual(len(loaded["test_runs"]), 24)
        self.assertEqual(len(set(run_ids)), 24)

    def test_journal_recovers_after_interrupted_state_write(self) -> None:
        failed = False

        def interrupted_writer(path: Path, text: str) -> None:
            nonlocal failed
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.name.startswith("attempt_") and path.suffix == ".json" and not failed:
                failed = True
                raise OSError("simulated interruption")
            path.write_text(text, encoding="utf-8")

        interrupted = self.make_store(interrupted_writer)
        with self.assertRaisesRegex(OSError, "simulated interruption"):
            interrupted.create(
                topic="algorithms",
                topic_generation=self.generation,
                problem=self.problem,
                workspace=self.workspace,
                language="python",
                activity_id="",
                purpose="placement",
                attempt_id="attempt_" + "3" * 32,
            )

        recovered = self.make_store().load("algorithms", "attempt_" + "3" * 32)
        self.assertEqual(recovered["status"], "active")
        events = list(
            interview_attempts.iter_events(
                self.store.events_path("algorithms", "attempt_" + "3" * 32)
            )
        )
        self.assertEqual([event["event_type"] for event in events], ["attempt_created"])

    def test_corruption_generation_change_and_symlink_are_rejected(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        self.generations["algorithms"] = "topic_" + "4" * 32
        with self.assertRaisesRegex(interview_attempts.AttemptError, "topic changed"):
            self.store.snapshot("algorithms", attempt_id)

        self.store.state_path("algorithms", attempt_id).write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(interview_attempts.AttemptError, "corrupted"):
            self.store.load("algorithms", attempt_id)

        other = self.root / "drills" / "algorithms" / "other.py"
        other.symlink_to(self.workspace)
        with self.assertRaisesRegex(interview_attempts.AttemptError, "unsafe"):
            self.store.create(
                topic="algorithms",
                topic_generation="topic_" + "4" * 32,
                problem=self.problem,
                workspace=other,
                language="python",
                activity_id="",
                purpose="practice",
            )

    def test_events_remain_idempotent_when_journal_recovery_replays(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        journal_path = self.store.journal_path("algorithms", attempt_id)
        events = list(
            interview_attempts.iter_events(
                self.store.events_path("algorithms", attempt_id)
            )
        )
        state = self.store.load("algorithms", attempt_id)
        journal_path.write_text(
            json.dumps({"record": state, "event": events[0]}),
            encoding="utf-8",
        )

        self.store.load("algorithms", attempt_id)
        replayed = list(
            interview_attempts.iter_events(
                self.store.events_path("algorithms", attempt_id)
            )
        )
        self.assertEqual(len(replayed), 1)


if __name__ == "__main__":
    unittest.main()
