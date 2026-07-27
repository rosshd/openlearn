"""Trusted reference implementations for openlearn-authored catalog problems.

These functions are maintainer code used only while validating packaged catalog
data. They are deliberately not executed through the learner-code runner.
"""

from __future__ import annotations

from collections import Counter


def pair_sum_sorted(values: list[int], target: int) -> list[int]:
    left = 0
    right = len(values) - 1
    while left < right:
        total = values[left] + values[right]
        if total == target:
            return [left, right]
        if total < target:
            left += 1
        else:
            right -= 1
    return [-1, -1]


def merge_busy_periods(intervals: list[list[int]]) -> list[list[int]]:
    if not intervals:
        return []
    ordered = sorted((start, end) for start, end in intervals)
    merged = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        if start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged


def longest_uniform_span_after_replacements(text: str, budget: int) -> int:
    counts: Counter[str] = Counter()
    left = 0
    most_frequent = 0
    best = 0
    for right, character in enumerate(text):
        counts[character] += 1
        most_frequent = max(most_frequent, counts[character])
        if right - left + 1 - most_frequent > budget:
            counts[text[left]] -= 1
            left += 1
        best = max(best, right - left + 1)
    return best


def connected_component_count(node_count: int, edges: list[list[int]]) -> int:
    parents = list(range(node_count))

    def root(node: int) -> int:
        while node != parents[node]:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    count = node_count
    for left, right in edges:
        left_root = root(left)
        right_root = root(right)
        if left_root != right_root:
            parents[right_root] = left_root
            count -= 1
    return count
