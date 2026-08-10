from __future__ import annotations

import copy
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

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
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            temporary.replace(path)

        return interview_attempts.AttemptStore(
            self.root,
            lock,
            writer or atomic_write,
            self.generations.get,
        )

    def create(self, **overrides):
        activity_id = str(overrides.get("activity_id") or "act_" + "2" * 32)
        values = {
            "topic": "algorithms",
            "topic_generation": self.generation,
            "problem": self.problem,
            "workspace": self.workspace,
            "language": "python",
            "activity_id": activity_id,
            "purpose": "practice",
            "profile_ref": "algorithms.interview.json",
            "clarification": "Can values repeat?",
            "plan": "Track the first index for each complement.",
            "activity_bundle": {
                "activity_id": activity_id,
                "revision": 1,
                "concept_ids": ["hash-maps"],
            },
        }
        values.update(overrides)
        return self.store.create(**values)

    def near_limit_record(
        self, created: dict[str, object], target_bytes: int
    ) -> dict[str, object]:
        record = copy.deepcopy(created)
        record["revision"] = int(record["revision"]) + 1
        entries = record["reasoning"]["entries"]
        self.assertIsInstance(entries, list)
        for index in range(interview_attempts.MAX_EVIDENCE_REFS):
            entry = {
                "entry_id": f"entry_{index + 1:032x}",
                "at": record["started_at"],
                "content": "x" * interview_attempts.MAX_TEXT,
                "run_id": None,
                "evidence_id": None,
                "kind": "reflection",
            }
            candidate = copy.deepcopy(record)
            candidate["reasoning"]["entries"].append(entry)
            full_size = len(interview_attempts._serialize_state(candidate))
            if full_size <= target_bytes:
                entries.append(entry)
                continue
            entry["content"] = "x"
            candidate = copy.deepcopy(record)
            candidate["reasoning"]["entries"].append(entry)
            minimum_size = len(interview_attempts._serialize_state(candidate))
            if minimum_size <= target_bytes:
                entry["content"] = "x" * min(
                    interview_attempts.MAX_TEXT,
                    target_bytes - minimum_size + 1,
                )
                entries.append(entry)
            break
        return interview_attempts.validate_attempt(record)

    def valid_event_log(
        self,
        attempt_id: str,
        timestamp: str,
        target_bytes: int,
        *,
        start_index: int = 10_000,
    ) -> bytes:
        maximum_padding = 15_900

        def event_line(index: int, padding: int) -> bytes:
            return interview_attempts._serialize_event(
                {
                    "schema_version": interview_attempts.EVENT_SCHEMA_VERSION,
                    "event_id": f"event_{index:032x}",
                    "attempt_id": attempt_id,
                    "attempt_revision": index,
                    "ts": timestamp,
                    "event_type": "attempt_filler",
                    "data": {"padding": "x" * padding},
                }
            )

        lines: list[bytes] = []
        total = 0
        index = start_index
        while total < target_bytes:
            maximum = event_line(index, maximum_padding)
            if total + len(maximum) <= target_bytes:
                lines.append(maximum)
                total += len(maximum)
                index += 1
                continue
            remaining = target_bytes - total
            minimum = event_line(index, 0)
            if remaining >= len(minimum):
                lines.append(event_line(index, remaining - len(minimum)))
                total = target_bytes
                break
            previous = lines.pop()
            total -= len(previous)
            first_minimum = event_line(index - 1, 0)
            second_minimum = event_line(index, 0)
            padding = (
                target_bytes
                - total
                - len(first_minimum)
                - len(second_minimum)
            )
            self.assertGreaterEqual(padding, 0)
            self.assertLessEqual(padding, maximum_padding * 2)
            first_padding = min(padding, maximum_padding)
            second_padding = padding - first_padding
            lines.extend(
                (
                    event_line(index - 1, first_padding),
                    event_line(index, second_padding),
                )
            )
            total = target_bytes
            break
        result = b"".join(lines)
        self.assertEqual(len(result), target_bytes)
        return result

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

        assisted = self.store.record_assistance(
            "algorithms", attempt_id, hint="Use a map."
        )
        reasoned = self.store.record_reasoning(
            "algorithms",
            attempt_id,
            complexity="O(n)",
            edge_cases="duplicate values",
            reflection="Track complements.",
        )
        evidenced = self.store.add_evidence(
            "algorithms", attempt_id, "evidence_review_1", kind="review"
        )
        replayed = self.store.add_evidence(
            "algorithms", attempt_id, "evidence_review_1", kind="review"
        )

        self.assertEqual(completed["disposition"], "solved_independently")
        self.assertEqual(assisted["status"], "completed")
        self.assertEqual(len(assisted["assistance"]["hints"]), 1)
        self.assertEqual(len(reasoned["reasoning"]["entries"]), 3)
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

    def test_concurrent_deterministic_start_has_one_pending_run(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        run_id = "run_" + "4" * 32

        def start(_index: int) -> str:
            _record, identifier = self.store.start_test(
                "algorithms", attempt_id, run_id=run_id
            )
            return identifier

        with ThreadPoolExecutor(max_workers=8) as executor:
            run_ids = list(executor.map(start, range(24)))

        loaded = self.store.load("algorithms", attempt_id)
        self.assertEqual(len(loaded["test_runs"]), 1)
        self.assertEqual(set(run_ids), {run_id})

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
            json.dumps(
                {
                    "schema_version": interview_attempts.JOURNAL_SCHEMA_VERSION,
                    "attempt_id": attempt_id,
                    "topic": "algorithms",
                    "topic_generation": self.generation,
                    "previous_revision": 0,
                    "next_revision": 1,
                    "created_at": events[0]["ts"],
                    "record": state,
                    "event": events[0],
                }
            ),
            encoding="utf-8",
        )

        self.store.load("algorithms", attempt_id)
        replayed = list(
            interview_attempts.iter_events(
                self.store.events_path("algorithms", attempt_id)
            )
        )
        self.assertEqual(len(replayed), 1)

    def test_topic_lock_precedes_attempt_lock_and_generation_rechecks_inside(self) -> None:
        acquisitions: list[str] = []
        locks: dict[str, threading.RLock] = {}
        generation = self.generation

        @contextmanager
        def lock(path: Path):
            nonlocal generation
            acquisitions.append(path.name)
            value = locks.setdefault(str(path), threading.RLock())
            with value:
                if path.name.startswith("attempt_"):
                    generation = "topic_" + "9" * 32
                yield

        def writer(path: Path, text: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        store = interview_attempts.AttemptStore(
            self.root, lock, writer, lambda _topic: generation
        )
        with self.assertRaisesRegex(interview_attempts.AttemptError, "topic changed"):
            store.create(
                topic="algorithms",
                topic_generation=self.generation,
                problem=self.problem,
                workspace=self.workspace,
                language="python",
                activity_id="",
                purpose="practice",
            )
        self.assertEqual(acquisitions[:2], ["algorithms.md", mock.ANY])
        self.assertTrue(acquisitions[1].startswith("attempt_"))
        self.assertFalse(list(store.topic_dir("algorithms").glob("attempt_*.json")))

    def test_mutation_rechecks_generation_after_topic_and_attempt_locks(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        original_events = list(
            interview_attempts.iter_events(
                self.store.events_path("algorithms", attempt_id)
            )
        )
        acquisitions: list[str] = []
        generation = self.generation

        @contextmanager
        def lock(path: Path):
            nonlocal generation
            acquisitions.append(path.name)
            value = self.locks.setdefault(str(path), threading.RLock())
            with value:
                if path.name == f"{attempt_id}.json":
                    generation = "topic_" + "8" * 32
                yield

        racing_store = interview_attempts.AttemptStore(
            self.root,
            lock,
            self.store.write_atomic,
            lambda _topic: generation,
        )
        with self.assertRaisesRegex(interview_attempts.AttemptError, "topic changed"):
            racing_store.record_assistance(
                "algorithms", attempt_id, hint="This must not be persisted."
            )

        self.assertEqual(acquisitions[:2], ["algorithms.md", f"{attempt_id}.json"])
        self.assertEqual(self.store.load("algorithms", attempt_id), created)
        self.assertEqual(
            list(
                interview_attempts.iter_events(
                    self.store.events_path("algorithms", attempt_id)
                )
            ),
            original_events,
        )

    def test_stale_journal_cannot_roll_back_or_append(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        current = self.store.snapshot("algorithms", attempt_id)
        original_events = list(
            interview_attempts.iter_events(
                self.store.events_path("algorithms", attempt_id)
            )
        )
        stale_event = original_events[0]
        stale_envelope = {
            "schema_version": interview_attempts.JOURNAL_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "topic": "algorithms",
            "topic_generation": self.generation,
            "previous_revision": 0,
            "next_revision": 1,
            "created_at": stale_event["ts"],
            "record": created,
            "event": stale_event,
        }
        self.store.journal_path("algorithms", attempt_id).write_text(
            json.dumps(stale_envelope), encoding="utf-8"
        )

        with self.assertRaisesRegex(interview_attempts.AttemptError, "stale"):
            self.store.load("algorithms", attempt_id)

        self.store.journal_path("algorithms", attempt_id).unlink()
        self.assertEqual(self.store.load("algorithms", attempt_id), current)
        self.assertEqual(
            list(
                interview_attempts.iter_events(
                    self.store.events_path("algorithms", attempt_id)
                )
            ),
            original_events,
        )

    def test_cross_attempt_and_conflicting_revision_journals_do_not_write_state(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        state_path = self.store.state_path("algorithms", attempt_id)
        journal_path = self.store.journal_path("algorithms", attempt_id)
        original_state = state_path.read_text(encoding="utf-8")
        original_events = list(
            interview_attempts.iter_events(
                self.store.events_path("algorithms", attempt_id)
            )
        )
        cross_attempt = "attempt_" + "7" * 32
        journal_path.write_text(
            json.dumps(
                {
                    "schema_version": interview_attempts.JOURNAL_SCHEMA_VERSION,
                    "attempt_id": cross_attempt,
                    "topic": "algorithms",
                    "topic_generation": self.generation,
                    "previous_revision": 1,
                    "next_revision": 2,
                    "created_at": created["last_active_at"],
                    "record": created,
                    "event": original_events[0],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(interview_attempts.AttemptError, "invalid"):
            self.store.load("algorithms", attempt_id)
        self.assertEqual(state_path.read_text(encoding="utf-8"), original_state)
        journal_path.unlink()

        next_record = json.loads(original_state)
        next_record["revision"] = 2
        next_record["clarification"] = "A journal update."
        conflicting_event = {
            "schema_version": interview_attempts.EVENT_SCHEMA_VERSION,
            "event_id": "event_" + "5" * 32,
            "attempt_id": attempt_id,
            "attempt_revision": 2,
            "ts": created["last_active_at"],
            "event_type": "attempt_conflict",
            "data": {},
        }
        events_path = self.store.events_path("algorithms", attempt_id)
        with events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(conflicting_event) + "\n")
        journal_event = {
            **conflicting_event,
            "event_id": "event_" + "6" * 32,
            "event_type": "attempt_update",
        }
        journal_path.write_text(
            json.dumps(
                {
                    "schema_version": interview_attempts.JOURNAL_SCHEMA_VERSION,
                    "attempt_id": attempt_id,
                    "topic": "algorithms",
                    "topic_generation": self.generation,
                    "previous_revision": 1,
                    "next_revision": 2,
                    "created_at": created["last_active_at"],
                    "record": next_record,
                    "event": journal_event,
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            interview_attempts.AttemptError, "revision already"
        ):
            self.store.load("algorithms", attempt_id)
        self.assertEqual(state_path.read_text(encoding="utf-8"), original_state)

    @unittest.skipIf(os.name == "nt", "POSIX no-follow descriptor race")
    def test_final_workspace_symlink_swap_fails_closed(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        outside = self.root.parent / "outside.py"
        outside.write_text("secret\n", encoding="utf-8")
        original_open = os.open
        swapped = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == self.workspace.name and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                self.workspace.unlink()
                self.workspace.symlink_to(outside)
            return original_open(path, flags, *args, **kwargs)

        with mock.patch.object(interview_attempts.os, "open", side_effect=racing_open):
            with self.assertRaisesRegex(
                interview_attempts.AttemptError, "read safely"
            ):
                self.store.snapshot("algorithms", attempt_id)

    @unittest.skipIf(os.name == "nt", "POSIX no-follow descriptor race")
    def test_workspace_parent_symlink_swap_fails_closed(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        topic_directory = self.workspace.parent
        saved_directory = topic_directory.with_name("algorithms-saved")
        outside_directory = self.root.parent / "outside"
        outside_directory.mkdir()
        (outside_directory / self.workspace.name).write_text(
            "secret\n", encoding="utf-8"
        )
        original_open = os.open
        swapped = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == "algorithms" and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                topic_directory.rename(saved_directory)
                topic_directory.symlink_to(outside_directory, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)

        try:
            with mock.patch.object(
                interview_attempts.os, "open", side_effect=racing_open
            ):
                with self.assertRaisesRegex(
                    interview_attempts.AttemptError, "parent could not be opened safely"
                ):
                    self.store.snapshot("algorithms", attempt_id)
        finally:
            if topic_directory.is_symlink():
                topic_directory.unlink()
            if saved_directory.exists():
                saved_directory.rename(topic_directory)

    def test_nested_corruption_and_oversized_events_fail_closed(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        state_path = self.store.state_path("algorithms", attempt_id)
        corrupted = dict(created)
        corrupted["snapshots"] = [{"snapshot_id": "wrong"}]
        state_path.write_text(json.dumps(corrupted), encoding="utf-8")
        with self.assertRaisesRegex(interview_attempts.AttemptError, "snapshot"):
            self.store.load("algorithms", attempt_id)

        events_path = self.store.events_path("algorithms", attempt_id)
        events_path.write_text("x" * (interview_attempts.MAX_EVENTS_BYTES + 1), encoding="utf-8")
        with self.assertRaisesRegex(interview_attempts.AttemptError, "too large"):
            list(interview_attempts.iter_events(events_path))

    def test_oversized_reasoning_event_never_publishes_a_journal(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        state_path = self.store.state_path("algorithms", attempt_id)
        state_before = state_path.read_text(encoding="utf-8")
        events_before = self.store.events_path(
            "algorithms", attempt_id
        ).read_text(encoding="utf-8")

        with self.assertRaisesRegex(interview_attempts.AttemptError, "event data"):
            self.store.record_reasoning(
                "algorithms",
                attempt_id,
                complexity="a" * interview_attempts.MAX_TEXT,
                edge_cases="b" * interview_attempts.MAX_TEXT,
            )

        self.assertFalse(self.store.journal_path("algorithms", attempt_id).exists())
        self.assertEqual(state_path.read_text(encoding="utf-8"), state_before)
        self.assertEqual(
            self.store.events_path("algorithms", attempt_id).read_text(encoding="utf-8"),
            events_before,
        )
        self.assertEqual(self.store.load("algorithms", attempt_id), created)

    def test_compact_state_crossing_old_pretty_limit_commits_and_reloads(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        record = self.near_limit_record(
            created, interview_attempts.MAX_STATE_BYTES - 64
        )
        compact = interview_attempts._serialize_state(record)
        pretty = (
            json.dumps(record, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        self.assertLessEqual(len(compact), interview_attempts.MAX_STATE_BYTES)
        self.assertGreater(len(pretty), interview_attempts.MAX_STATE_BYTES)

        self.store._commit(
            record,
            "attempt_near_state_limit",
            {},
            previous_revision=int(created["revision"]),
        )

        state_path = self.store.state_path("algorithms", attempt_id)
        self.assertEqual(state_path.read_bytes(), compact)
        self.assertEqual(self.store.load("algorithms", attempt_id), record)

    def test_large_journal_uses_same_write_and_read_cap(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        record = self.near_limit_record(
            created, interview_attempts.MAX_STATE_BYTES - 64
        )
        event = {
            "schema_version": interview_attempts.EVENT_SCHEMA_VERSION,
            "event_id": "event_" + "d" * 32,
            "attempt_id": attempt_id,
            "attempt_revision": record["revision"],
            "ts": record["last_active_at"],
            "event_type": "attempt_large_journal",
            "data": {"padding": "z" * 15_900},
        }
        journal = {
            "schema_version": interview_attempts.JOURNAL_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "topic": "algorithms",
            "topic_generation": self.generation,
            "previous_revision": created["revision"],
            "next_revision": record["revision"],
            "created_at": record["last_active_at"],
            "record": record,
            "event": event,
        }
        encoded = interview_attempts._serialize_journal(journal)
        self.assertGreater(len(encoded), 528_000)
        self.assertLess(len(encoded), 576_000)
        self.assertLessEqual(len(encoded), interview_attempts.MAX_JOURNAL_BYTES)

        self.store._commit(
            record,
            "attempt_large_journal",
            event["data"],
            event_id=str(event["event_id"]),
            previous_revision=int(created["revision"]),
        )

        self.assertEqual(self.store.load("algorithms", attempt_id), record)
        self.assertFalse(self.store.journal_path("algorithms", attempt_id).exists())

    def test_rejected_near_limit_mutation_is_atomic_and_remains_loadable(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        record = self.near_limit_record(
            created, interview_attempts.MAX_STATE_BYTES - 64
        )
        self.store._commit(
            record,
            "attempt_near_state_limit",
            {},
            previous_revision=int(created["revision"]),
        )
        state_path = self.store.state_path("algorithms", attempt_id)
        events_path = self.store.events_path("algorithms", attempt_id)
        state_before = state_path.read_bytes()
        events_before = events_path.read_bytes()

        with self.assertRaisesRegex(interview_attempts.AttemptError, "too large"):
            self.store.record_reasoning(
                "algorithms",
                attempt_id,
                reflection="y" * 1_000,
            )

        self.assertFalse(self.store.journal_path("algorithms", attempt_id).exists())
        self.assertEqual(state_path.read_bytes(), state_before)
        self.assertEqual(events_path.read_bytes(), events_before)
        self.assertEqual(self.store.load("algorithms", attempt_id), record)

    def test_near_full_event_log_rejects_before_journal_or_state_write(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        events_path = self.store.events_path("algorithms", attempt_id)
        events_path.write_bytes(
            self.valid_event_log(
                attempt_id,
                str(created["last_active_at"]),
                1_999_990,
            )
        )
        state_path = self.store.state_path("algorithms", attempt_id)
        state_before = state_path.read_bytes()
        events_before = events_path.read_bytes()
        updated = copy.deepcopy(created)
        updated["revision"] = 2
        updated["clarification"] = "This must be rejected atomically."

        with self.assertRaisesRegex(
            interview_attempts.AttemptError, "event log is too large"
        ):
            self.store._commit(
                updated,
                "attempt_event_limit",
                {},
                event_id="event_" + "e" * 32,
                previous_revision=1,
            )

        self.assertFalse(self.store.journal_path("algorithms", attempt_id).exists())
        self.assertEqual(state_path.read_bytes(), state_before)
        self.assertEqual(events_path.read_bytes(), events_before)
        self.assertEqual(self.store.load("algorithms", attempt_id), created)

    def test_event_log_exact_under_at_and_over_boundaries_are_atomic(self) -> None:
        for delta in (-1, 0, 1):
            with self.subTest(delta=delta):
                created = self.create()
                attempt_id = str(created["attempt_id"])
                updated = copy.deepcopy(created)
                updated["revision"] = 2
                updated["clarification"] = f"boundary {delta}"
                event_id = f"event_{delta + 2:032x}"
                preview = interview_attempts._serialize_event(
                    {
                        "schema_version": interview_attempts.EVENT_SCHEMA_VERSION,
                        "event_id": event_id,
                        "attempt_id": attempt_id,
                        "attempt_revision": 2,
                        "ts": created["last_active_at"],
                        "event_type": "attempt_event_boundary",
                        "data": {},
                    }
                )
                target = interview_attempts.MAX_EVENTS_BYTES - len(preview) + delta
                events_path = self.store.events_path("algorithms", attempt_id)
                events_path.write_bytes(
                    self.valid_event_log(
                        attempt_id,
                        str(created["last_active_at"]),
                        target,
                    )
                )
                state_path = self.store.state_path("algorithms", attempt_id)
                state_before = state_path.read_bytes()
                events_before = events_path.read_bytes()
                self.store.now = lambda: datetime.fromisoformat(
                    str(created["last_active_at"])
                )

                if delta <= 0:
                    self.store._commit(
                        updated,
                        "attempt_event_boundary",
                        {},
                        event_id=event_id,
                        previous_revision=1,
                    )
                    self.assertEqual(
                        events_path.stat().st_size,
                        interview_attempts.MAX_EVENTS_BYTES + delta,
                    )
                    self.assertEqual(self.store.load("algorithms", attempt_id), updated)
                else:
                    with self.assertRaisesRegex(
                        interview_attempts.AttemptError, "event log is too large"
                    ):
                        self.store._commit(
                            updated,
                            "attempt_event_boundary",
                            {},
                            event_id=event_id,
                            previous_revision=1,
                        )
                    self.assertFalse(
                        self.store.journal_path("algorithms", attempt_id).exists()
                    )
                    self.assertEqual(state_path.read_bytes(), state_before)
                    self.assertEqual(events_path.read_bytes(), events_before)
                    self.assertEqual(self.store.load("algorithms", attempt_id), created)

    def test_duplicate_event_at_capacity_is_not_double_counted(self) -> None:
        created = self.create()
        attempt_id = str(created["attempt_id"])
        updated = copy.deepcopy(created)
        updated["revision"] = 2
        updated["clarification"] = "duplicate event replay"
        self.store.now = lambda: datetime.fromisoformat(
            str(created["last_active_at"])
        )
        duplicate = {
            "schema_version": interview_attempts.EVENT_SCHEMA_VERSION,
            "event_id": "event_" + "f" * 32,
            "attempt_id": attempt_id,
            "attempt_revision": 2,
            "ts": created["last_active_at"],
            "event_type": "attempt_duplicate_boundary",
            "data": {},
        }
        duplicate_line = interview_attempts._serialize_event(duplicate)
        events_path = self.store.events_path("algorithms", attempt_id)
        filler = self.valid_event_log(
            attempt_id,
            str(created["last_active_at"]),
            interview_attempts.MAX_EVENTS_BYTES - len(duplicate_line),
        )
        events_before = filler + duplicate_line
        events_path.write_bytes(events_before)

        self.store._commit(
            updated,
            "attempt_duplicate_boundary",
            {},
            event_id=str(duplicate["event_id"]),
            previous_revision=1,
        )

        self.assertEqual(events_path.read_bytes(), events_before)
        self.assertEqual(self.store.load("algorithms", attempt_id), updated)

    def test_verified_independent_transfer_clears_effective_scaffolding(self) -> None:
        assisted = self.create()
        assisted_id = str(assisted["attempt_id"])
        self.store.record_assistance(
            "algorithms", assisted_id, full_solution_exposed=True
        )
        assisted = self.store.complete("algorithms", assisted_id)
        transfer_problem = {**self.problem, "problem_id": "two-sum-transfer", "problem_checksum": "b" * 64}
        transfer_workspace = self.workspace.with_name("transfer.py")
        transfer_workspace.write_text("def transfer():\n    return True\n", encoding="utf-8")
        transfer = self.create(problem=transfer_problem, workspace=transfer_workspace)
        transfer_id = str(transfer["attempt_id"])
        _transfer, run_id = self.store.start_test("algorithms", transfer_id)
        self.store.finish_test(
            "algorithms", transfer_id, run_id, outcome="passed"
        )
        transfer = self.store.add_evidence(
            "algorithms", transfer_id, "evidence_transfer_pass", kind="pytest_result"
        )
        transfer = self.store.queue_feedback(
            "algorithms",
            transfer_id,
            run_id,
            "evidence_transfer_pass",
            hint_index=0,
            prompt="Explain the transfer.",
        )
        feedback_id = str(transfer["feedback"]["pending"]["feedback_id"])
        self.store.save_feedback_answer(
            "algorithms", transfer_id, feedback_id, "Independent solution."
        )
        for step in sorted(interview_attempts.FEEDBACK_STEPS):
            self.store.mark_feedback_step(
                "algorithms", transfer_id, feedback_id, step
            )
        self.store.deliver_feedback("algorithms", transfer_id, feedback_id)
        self.store.complete("algorithms", transfer_id)

        cleared = self.store.record_independent_transfer(
            "algorithms",
            assisted_id,
            evidence_id="evidence_transfer_pass",
            transfer_attempt_id=transfer_id,
            problem=transfer_problem,
        )

        self.assertEqual(assisted["disposition"], "solved_with_help")
        self.assertEqual(
            interview_attempts.effective_disposition(cleared),
            "solved_independently",
        )
        self.assertEqual(
            len(cleared["follow_up"]["independent_transfer_evidence"]), 1
        )

    def test_transfer_rejects_arbitrary_or_wrong_skill_evidence(self) -> None:
        assisted = self.create()
        assisted_id = str(assisted["attempt_id"])
        self.store.record_assistance(
            "algorithms", assisted_id, full_solution_exposed=True
        )
        self.store.complete("algorithms", assisted_id)
        transfer_problem = {
            **self.problem,
            "problem_id": "unrelated-transfer",
            "problem_checksum": "c" * 64,
        }
        transfer_workspace = self.workspace.with_name("unrelated.py")
        transfer_workspace.write_text("def unrelated():\n    return True\n", encoding="utf-8")
        transfer_activity = {
            "activity_id": "act_" + "9" * 32,
            "revision": 1,
            "concept_ids": ["graphs"],
        }
        transfer = self.create(
            problem=transfer_problem,
            workspace=transfer_workspace,
            activity_id=transfer_activity["activity_id"],
            activity_bundle=transfer_activity,
        )
        transfer_id = str(transfer["attempt_id"])
        transfer = self.store.add_evidence(
            "algorithms", transfer_id, "evidence_arbitrary", kind="review"
        )
        self.store.complete("algorithms", transfer_id)

        with self.assertRaisesRegex(
            interview_attempts.AttemptError, "passed novel problem"
        ):
            self.store.record_independent_transfer(
                "algorithms",
                assisted_id,
                evidence_id="evidence_arbitrary",
                transfer_attempt_id=transfer_id,
                problem=transfer_problem,
            )


if __name__ == "__main__":
    unittest.main()
