"""Read-only aggregation helpers for the stats dashboard.

Everything here is a pure function over already-parsed topic metadata and
event-log records.
Nothing in this module reads or writes files.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import cast

from openlearn.text import concept_key

DEFAULT_SESSION_GAP_MINUTES = 30.0
MIN_SESSION_MINUTES = 1.0
FORECAST_WEEK_DAYS = 7


def parse_event_timestamp(value: object) -> datetime | None:
    """Parse an event `ts` value to an aware UTC datetime; naive values are treated as UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def event_timestamps(events: Sequence[object]) -> list[datetime]:
    stamps = (parse_event_timestamp(event.get("ts")) for event in events if isinstance(event, dict))
    return sorted(ts for ts in stamps if ts is not None)


def activity_dates(timestamps: list[datetime]) -> set[date]:
    return {ts.date() for ts in timestamps}


def current_streak(dates: set[date], today: date) -> int:
    """Consecutive active days ending today, or ending yesterday if today is inactive yet."""
    day = today if today in dates else today - timedelta(days=1)
    streak = 0
    while day in dates:
        streak += 1
        day -= timedelta(days=1)
    return streak


def longest_streak(dates: set[date]) -> int:
    longest = 0
    for day in dates:
        if day - timedelta(days=1) in dates:
            continue
        length = 1
        while day + timedelta(days=length) in dates:
            length += 1
        longest = max(longest, length)
    return longest


def session_spans(
    timestamps: list[datetime],
    gap_minutes: float = DEFAULT_SESSION_GAP_MINUTES,
) -> list[tuple[datetime, datetime]]:
    """Group event timestamps into sessions split at gaps longer than `gap_minutes`."""
    spans: list[tuple[datetime, datetime]] = []
    gap = timedelta(minutes=gap_minutes)
    for ts in sorted(timestamps):
        if spans and ts - spans[-1][1] <= gap:
            spans[-1] = (spans[-1][0], ts)
        else:
            spans.append((ts, ts))
    return spans


def minutes_in_window(
    spans: list[tuple[datetime, datetime]],
    start: datetime,
    end: datetime,
) -> float:
    """Estimated study minutes for the session spans clipped to [start, end].

    Each span that overlaps the window counts at least MIN_SESSION_MINUTES, so
    a session with a single event still registers as activity.
    """
    total = 0.0
    for span_start, span_end in spans:
        clipped_start = max(span_start, start)
        clipped_end = min(span_end, end)
        if clipped_end < clipped_start:
            continue
        total += max((clipped_end - clipped_start).total_seconds() / 60, MIN_SESSION_MINUTES)
    return round(total, 1)


def week_window(now: datetime) -> tuple[datetime, datetime]:
    """The current week (Monday 00:00 UTC through `now`)."""
    moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    monday = moment.date() - timedelta(days=moment.weekday())
    start = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
    return start, moment


def known_concept_keys(metadata: dict[str, object]) -> set[str]:
    known = metadata.get("known")
    if not isinstance(known, list):
        return set()
    return {concept_key(item) for item in known if isinstance(item, str) and item.strip()}


def unit_mastery(metadata: dict[str, object]) -> list[dict[str, object]]:
    """Per-unit mastery rows: known/total concepts and percent, in course order."""
    units = metadata.get("course_units")
    if not isinstance(units, list):
        return []
    known_keys = known_concept_keys(metadata)
    rows: list[dict[str, object]] = []
    for item in units:
        if not isinstance(item, dict):
            continue
        raw_concepts = item.get("concepts")
        concepts = (
            [entry for entry in raw_concepts if isinstance(entry, dict)]
            if isinstance(raw_concepts, list)
            else []
        )
        known_count = 0
        for concept in concepts:
            keys = {
                concept_key(value)
                for value in (concept.get("id"), concept.get("label"))
                if isinstance(value, str) and value.strip()
            }
            if keys & known_keys:
                known_count += 1
        total = len(concepts)
        unit_number = item.get("unit")
        title = item.get("title")
        rows.append(
            {
                "unit": unit_number if isinstance(unit_number, int) else len(rows) + 1,
                "title": title.strip() if isinstance(title, str) else "",
                "known": known_count,
                "total": total,
                "percent": round(known_count / total * 100) if total else 0,
            }
        )
    return rows


def review_forecast(metadata: dict[str, object], today: date) -> dict[str, int]:
    """Bucket review_due entries into due today, within a week, and later.

    Entries without a parseable due date follow normalize_review_due_metadata and
    count as due today.
    """
    counts = {"due_today": 0, "due_this_week": 0, "due_later": 0}
    items = metadata.get("review_due")
    if not isinstance(items, list):
        return counts
    for item in items:
        if isinstance(item, str):
            due = today if item.strip() else None
        elif isinstance(item, dict):
            due_value = item.get("due")
            if isinstance(due_value, str):
                try:
                    due = date.fromisoformat(due_value.strip())
                except ValueError:
                    due = today
            else:
                due = today
        else:
            due = None
        if due is None:
            continue
        if due <= today:
            counts["due_today"] += 1
        elif due <= today + timedelta(days=FORECAST_WEEK_DAYS):
            counts["due_this_week"] += 1
        else:
            counts["due_later"] += 1
    return counts


def combine_forecasts(forecasts: list[dict[str, int]]) -> dict[str, int]:
    combined = {"due_today": 0, "due_this_week": 0, "due_later": 0}
    for forecast in forecasts:
        for key in combined:
            combined[key] += int(forecast.get(key, 0))
    return combined


def total_mastery(rows: list[dict[str, object]]) -> tuple[int, int, int]:
    known = sum(int(cast(int | str | float, row.get("known", 0))) for row in rows)
    total = sum(int(cast(int | str | float, row.get("total", 0))) for row in rows)
    percent = round(known / total * 100) if total else 0
    return known, total, percent


def format_days(value: int) -> str:
    return f"{value} {'day' if value == 1 else 'days'}"


def shareable_summary(
    label: str,
    *,
    streak: int,
    longest_streak: int,
    weekly_minutes: float,
    forecast: dict[str, int],
    mastery_rows: list[dict[str, object]],
) -> str:
    known, total, percent = total_mastery(mastery_rows)
    minutes = f"{weekly_minutes:g}"
    return "\n".join(
        [
            f"openlearn progress - {label}",
            f"Streak: {format_days(streak)} current, {format_days(longest_streak)} longest",
            f"Study this week: {minutes} min",
            f"Mastery: {known}/{total} concepts ({percent}%)",
            (
                f"Reviews: {forecast.get('due_today', 0)} due now, "
                f"{forecast.get('due_this_week', 0)} in the next 7 days, "
                f"{forecast.get('due_later', 0)} later"
            ),
        ]
    )
