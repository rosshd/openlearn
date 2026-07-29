# Interview-prep learner journey

This workflow exercises the normal public CLI from profile setup through an observable first lesson.
Use an isolated home so the replay cannot read or modify personal learner state or provider configuration.

```bash
export OPENLEARN_HOME="$(mktemp -d)"
unset OPENAI_API_KEY OPENLEARN_BASE_URL OPENLEARN_MOCK
openlearn new "Leetcode Sweep" \
  --goal "Build consistent coding interview practice" \
  --interview-prep
```

Accept the profile defaults, confirm creation, and start the offline placement.
At the placement prompts, enter the following learner responses.

```text
calibration> I have completed data structures and algorithims college course months ago, and have done leetcoding on and off for the past 2 years, but can never stay consistant. I intern at state farm currently, and have some outside projects I work on like an AI tutor application and a guitar digital tuning pedal.
clarification> How is the input given to us? is it an array or text separated by commas? what would you like for me to return when I find a solution?
plan> I would use a sliding window with a hashmap to keep track of amounts of charactors. When they are all unique, I would return the result
implementation>
```

The blank implementation must keep the placement at implementation and print paste, editor, skip, baseline, and stop guidance.
Enter the fixed multiline solution and finish with `/done`.

```python
def first_unique_window(text, width):
    if width <= 0 or width > len(text):
        return -1
    counts = {}
    left = 0
    for right, char in enumerate(text):
        counts[char] = counts.get(char, 0) + 1
        if right - left + 1 > width:
            old = text[left]
            counts[old] -= 1
            if counts[old] == 0:
                del counts[old]
            left += 1
        if right - left + 1 == width and len(counts) == width:
            return left
    return -1
/done
```

Complete the remaining stages with representative tests, `O(n)` time and `O(width)` space, and a streaming follow-up that retains only the current window and counts.
Placement must finish provisional with seven evidence references and no mastery update.

With the remote provider still unconfigured, run:

```bash
openlearn resume leetcode-sweep
```

The command must show `Placement: provisional (7/7)`, omit `Where you left off` and `No previous session yet`, confirm that all work is saved, and give `openlearn config set-key` plus the resume command.

Enable deterministic mock teaching and resume again:

```bash
export OPENLEARN_MOCK=1
openlearn resume leetcode-sweep
```

The command must go directly to `Course outline` without asking for the legacy optional placement quiz.
Accept the outline and confirm that `First lesson` plus visible lesson content is rendered.

Finally, verify durable placement state:

```bash
openlearn interview placement leetcode-sweep status
```

Expected status includes `Placement: provisional` and `evidence 7/7`.
