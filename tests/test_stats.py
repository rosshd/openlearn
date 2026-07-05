import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openlearn import stats


def ts(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)


class TimestampTests(unittest.TestCase):
    def test_parses_utc_and_naive_timestamps(self) -> None:
        aware = stats.parse_event_timestamp("2026-07-01T10:00:00+00:00")
        zulu = stats.parse_event_timestamp("2026-07-01T10:00:00Z")
        naive = stats.parse_event_timestamp("2026-07-01T10:00:00")

        self.assertEqual(aware, ts(1, 10))
        self.assertEqual(zulu, ts(1, 10))
        self.assertEqual(naive, ts(1, 10))

    def test_rejects_invalid_values(self) -> None:
        self.assertIsNone(stats.parse_event_timestamp("not a date"))
        self.assertIsNone(stats.parse_event_timestamp(""))
        self.assertIsNone(stats.parse_event_timestamp(None))
        self.assertIsNone(stats.parse_event_timestamp(12345))

    def test_event_timestamps_sorted_and_filtered(self) -> None:
        events = [
            {"ts": "2026-07-02T09:00:00Z"},
            {"ts": "bogus"},
            {"ts": "2026-07-01T09:00:00Z"},
            "not-a-dict",
            {},
        ]

        self.assertEqual(stats.event_timestamps(events), [ts(1, 9), ts(2, 9)])


class StreakTests(unittest.TestCase):
    def test_day_count_uses_singular_and_plural_labels(self) -> None:
        self.assertEqual(stats.format_days(1), "1 day")
        self.assertEqual(stats.format_days(2), "2 days")

    def test_streak_counts_consecutive_days_ending_today(self) -> None:
        dates = {date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)}

        self.assertEqual(stats.current_streak(dates, date(2026, 7, 3)), 3)

    def test_streak_alive_when_today_has_no_session_yet(self) -> None:
        dates = {date(2026, 7, 1), date(2026, 7, 2)}

        self.assertEqual(stats.current_streak(dates, date(2026, 7, 3)), 2)

    def test_streak_broken_by_gap(self) -> None:
        dates = {date(2026, 6, 28), date(2026, 6, 29), date(2026, 7, 2)}

        self.assertEqual(stats.current_streak(dates, date(2026, 7, 2)), 1)

    def test_streak_zero_after_two_idle_days(self) -> None:
        dates = {date(2026, 6, 30)}

        self.assertEqual(stats.current_streak(dates, date(2026, 7, 2)), 0)

    def test_streak_empty_dates(self) -> None:
        self.assertEqual(stats.current_streak(set(), date(2026, 7, 2)), 0)

    def test_longest_streak_across_gaps(self) -> None:
        dates = {
            date(2026, 6, 1),
            date(2026, 6, 2),
            date(2026, 6, 3),
            date(2026, 6, 10),
            date(2026, 6, 11),
        }

        self.assertEqual(stats.longest_streak(dates), 3)
        self.assertEqual(stats.longest_streak(set()), 0)


class SessionMinutesTests(unittest.TestCase):
    def test_spans_split_on_gap(self) -> None:
        stamps = [ts(1, 9, 0), ts(1, 9, 20), ts(1, 9, 40), ts(1, 14, 0)]

        spans = stats.session_spans(stamps, gap_minutes=30)

        self.assertEqual(spans, [(ts(1, 9, 0), ts(1, 9, 40)), (ts(1, 14, 0), ts(1, 14, 0))])

    def test_minutes_in_window_clips_and_floors(self) -> None:
        spans = [(ts(1, 9, 0), ts(1, 9, 40)), (ts(1, 14, 0), ts(1, 14, 0))]

        minutes = stats.minutes_in_window(spans, ts(1, 0), ts(1, 23))

        self.assertEqual(minutes, 41.0)

    def test_minutes_in_window_excludes_outside_spans(self) -> None:
        spans = [(ts(1, 9, 0), ts(1, 9, 40))]

        self.assertEqual(stats.minutes_in_window(spans, ts(2, 0), ts(2, 23)), 0.0)
        self.assertEqual(stats.minutes_in_window([], ts(1, 0), ts(1, 23)), 0.0)

    def test_minutes_in_window_partial_overlap(self) -> None:
        spans = [(ts(1, 9, 0), ts(1, 10, 0))]

        self.assertEqual(stats.minutes_in_window(spans, ts(1, 9, 30), ts(1, 23)), 30.0)

    def test_week_window_starts_monday_utc(self) -> None:
        now = datetime(2026, 7, 2, 15, 30, tzinfo=timezone.utc)

        start, end = stats.week_window(now)

        self.assertEqual(start, datetime(2026, 6, 29, tzinfo=timezone.utc))
        self.assertEqual(end, now)

    def test_week_window_accepts_naive_now(self) -> None:
        start, end = stats.week_window(datetime(2026, 6, 29, 0, 5))

        self.assertEqual(start, datetime(2026, 6, 29, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 6, 29, 0, 5, tzinfo=timezone.utc))


class UnitMasteryTests(unittest.TestCase):
    def fixture_metadata(self) -> dict[str, object]:
        return {
            "known": ["Normal Mode", "motions!", "  "],
            "course_units": [
                {
                    "unit": 1,
                    "title": "Vim Basics",
                    "concepts": [
                        {"id": "normal-mode", "label": "Normal mode"},
                        {"id": "motions", "label": "Motions"},
                        {"id": "registers", "label": "Registers"},
                    ],
                },
                {
                    "unit": 2,
                    "title": "Editing",
                    "concepts": [{"id": "macros", "label": "Macros"}],
                },
                {"unit": 3, "title": "No Concepts", "concepts": []},
            ],
        }

    def test_mastery_percentages_from_fixture(self) -> None:
        rows = stats.unit_mastery(self.fixture_metadata())

        self.assertEqual(len(rows), 3)
        self.assertEqual(
            rows[0],
            {"unit": 1, "title": "Vim Basics", "known": 2, "total": 3, "percent": 67},
        )
        self.assertEqual(
            rows[1],
            {"unit": 2, "title": "Editing", "known": 0, "total": 1, "percent": 0},
        )
        self.assertEqual(
            rows[2],
            {"unit": 3, "title": "No Concepts", "known": 0, "total": 0, "percent": 0},
        )

    def test_mastery_handles_missing_or_malformed_data(self) -> None:
        self.assertEqual(stats.unit_mastery({}), [])
        self.assertEqual(stats.unit_mastery({"course_units": "oops"}), [])

        rows = stats.unit_mastery(
            {"known": "oops", "course_units": [{"unit": 1, "title": "A"}, "junk"]}
        )

        self.assertEqual(rows, [{"unit": 1, "title": "A", "known": 0, "total": 0, "percent": 0}])


class ReviewForecastTests(unittest.TestCase):
    def test_forecast_bucketing(self) -> None:
        today = date(2026, 7, 2)
        metadata = {
            "review_due": [
                {"concept": "overdue", "due": "2026-06-30"},
                {"concept": "today", "due": "2026-07-02"},
                {"concept": "in-week", "due": "2026-07-09"},
                {"concept": "later", "due": "2026-07-10"},
                {"concept": "bad-date", "due": "soon"},
                {"concept": "no-date"},
                "legacy-string",
                42,
            ]
        }

        counts = stats.review_forecast(metadata, today)

        self.assertEqual(counts, {"due_today": 5, "due_this_week": 1, "due_later": 1})

    def test_forecast_empty_states(self) -> None:
        empty = {"due_today": 0, "due_this_week": 0, "due_later": 0}

        self.assertEqual(stats.review_forecast({}, date(2026, 7, 2)), empty)
        self.assertEqual(stats.review_forecast({"review_due": "oops"}, date(2026, 7, 2)), empty)

    def test_combine_forecasts(self) -> None:
        combined = stats.combine_forecasts(
            [
                {"due_today": 1, "due_this_week": 2, "due_later": 0},
                {"due_today": 0, "due_this_week": 1, "due_later": 3},
                {},
            ]
        )

        self.assertEqual(combined, {"due_today": 1, "due_this_week": 3, "due_later": 3})


if __name__ == "__main__":
    unittest.main()
