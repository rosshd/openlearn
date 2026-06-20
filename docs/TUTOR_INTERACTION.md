# Tutor–Student Interaction Design

How openlearn turns a frontier LLM into the most effective tutor possible. This is the
design that the version phase-plans implement. It is grounded in the evidence in
[LEARNING_SCIENCE.md](LEARNING_SCIENCE.md); the most load-bearing sources are condensed at
the bottom of this file.

> **Thesis.** The model is not the moat. Every competitor has the same frontier LLMs. The
> moat is the *closed loop*: a persistent learner model + a pedagogical policy that selects
> the right move for the learner's state + measurement of whether learning actually happened,
> feeding back into the policy. Nobody ships that. We will.

---

## 1. Why this beats the alternatives

General AI chat (ChatGPT, Claude.ai), "study modes," and most LLM tutors share three
failure modes, all of which the research predicts:

1. **They optimize for helpfulness, which is the gaming-the-system failure mode.** A helpful
   assistant gives the answer. Baker et al. (2004): students who extract answers learn ~⅔ as
   much. A model tuned to be maximally helpful is, pedagogically, tuned to *prevent* learning.
2. **They have no memory of the learner.** No mastery model, no spaced retrieval, no record of
   what was shaky last week. Every session starts cold.
3. **They never measure whether learning happened.** They optimize for in-session satisfaction
   (the fluency illusion — Bjork & Bjork 2011), which is uncorrelated or *inversely* correlated
   with durable learning.

openlearn's differentiators map one-to-one onto these:

| Their failure | Our design response |
|---|---|
| Answer-giving (gaming) | Ungameable checks + attempt-gated help (§5) |
| No learner memory | Persistent local learner model (§3) |
| No measurement | Eval loop on delayed retrieval, not in-session feel (§7) |
| One-size prompt | State→move policy selecting the strategy per turn (§6) |

---

## 2. The learner model (state we track)

Per topic (in the Markdown metadata) and per concept. Bold = new instrumentation this design
adds beyond what v0.6.0 tracks.

- **Per concept:** attempts, rolling correct-rate, last-seen, **misconception tags**, SRS due
  date, mastery flag (mastery = a *passed transfer/production* check, not a single correct).
- **Per unit:** `difficulty` (1–10, adaptive), `difficulty_locked`.
- **Session/topic:** `consecutive_correct/misses`, `last_answer_score`, **rolling pass rate
  over the last N checks**, `difficulty_tier`.
- **Per answer (transient, drives gaming detection):** **response latency**, **verbatim
  overlap with recently shown text**, whether help was used before the attempt.
- **Calibration:** stated confidence vs. actual outcome (when collected).
- **Goal/rigor:** `mastery_profile` (`efficient` / `proficient` / `deep`) derived from the
  learner's goal — sets the *depth bar*, not the anti-gaming floor. The same material is held
  to a different standard for an exam-cramming student vs. a researcher. Parameterizes the
  mastery gate, advancement speed, and how hard the mastering-tier policy pushes. See
  V0.7.0.md Contract 4.

The learner model is the asset. It is also the eventual training-data asset (§8).

---

## 3. The per-turn interaction loop

Every learner message flows through this pipeline. Steps 2–5 are pure logic + a single judge
call; step 6 is the tutor generation.

```
1. INGEST     learner message + latency + the text we last showed
2. CLASSIFY   answer-to-pending-check | question | request | confusion/impasse signal
3. JUDGE      score(0-1), status, misconception, gap  (the most important component — §4)
4. DETECT     gaming signal = fast AND high verbatim-overlap AND/OR help-before-attempt (§5)
5. UPDATE     concept mastery, rolling pass-rate, counters, difficulty, calibration
6. SELECT     move = policy(tier, unit_difficulty, gaming, impasse)  (§6)
7. GENERATE   tutor turn that executes the move, under format + ungameable-question rules
8. GATE       schedule spaced retrieval; advance unit only if mastery gate passed (§6)
```

The current code already does a thin version of this (metadata extractor = a weak judge;
`select_check_mode` = a partial policy). The plan is to harden each step and close the loop.

---

## 4. The judge (answer evaluation) — the highest-leverage component

**If the judge is wrong, the whole adaptive engine drives off a cliff.** Difficulty, tier,
mastery, and move selection all consume its output. This is where to spend the most effort and
the most eval budget.

Requirements:
- Output a structured verdict: `score` (0–1), `status`, **`misconception`** (what specific
  wrong model the answer reveals, not just "wrong"), `gap` (prerequisite concept), and a
  **`gameable` flag** (could this answer have been produced by copying shown text?).
- Be calibrated: a score of 0.6 must mean the same thing across topics. Calibration is itself
  testable — see §7.
- Distinguish *recognition* from *production*. A correct multiple-choice pick is weaker
  evidence than a correct free-response explanation.
- Never contradict a stored answer key (already enforced for MC).

Design choice: keep the judge as a separate, cheap, single-purpose call with its own system
prompt (`METADATA_EXTRACTOR_SYSTEM` is the seed). It can later be the first component we
distill to a smaller/cheaper model once we have labeled data (§8) — judging is a narrower task
than tutoring and is the most cost-sensitive (it runs every turn).

---

## 5. Ungameable checks (the anti-shallow-comprehension core)

This is Ross's central concern — "sees the material, copies the answer, moves on." Defense in
depth:

1. **Question design (primary).** Checks must require *production/transformation*, not
   location. A question whose answer appears verbatim in the just-shown text is banned. Prefer:
   paraphrase in your own words, apply to a *new* example, predict an outcome, explain *why*,
   find the edge case. (Generation Effect; Elaborative Interrogation.)
2. **Attempt-gated help.** Hints/worked examples are withheld until a genuine attempt. This
   makes help non-cyclable for answers. (Productive Failure; Impasse-Driven Learning.)
3. **Behavioral detection (backstop).** Pure-logic signal: an answer that is (a) returned very
   fast and (b) has high n-gram overlap with the text we just showed is *suspect*. Suspect
   "correct" answers do **not** advance the concept; they trigger an immediate transfer
   question on the same concept and lower mastery confidence. (Gaming the System.)
4. **Mastery via transfer.** A concept is only "known" when answered correctly in a context
   *different* from where it was taught. Reproduction ≠ comprehension.

Detection (step 4) is pure logic and testable; it does not need the LLM.

---

## 6. Move-selection policy (state → strategy)

The policy is the operationalization of the **Adaptive Strategy Selection** synthesis in
LEARNING_SCIENCE.md. The default stance is **elicit, don't tell** (Chi et al. 2001: students
learned as much when tutors were forbidden to explain). Telling is the fallback for an impasse
the learner cannot resolve by prompting.

| Learner state | Goal | Move |
|---|---|---|
| **Struggling** (misses≥2 / score<0.35 / pass<~70%) | Reduce load, rebuild | One sub-concept; attempt → then worked example; specific, faded help |
| **On track** (~0.5–0.7 score, ~80–85% pass) | Hold here | Elicit; production/transfer Qs; "why"/"what if" probes; difficulty steady |
| **Mastering** (≥3 correct, score≥0.8, pass>~90%) | Escape fluency illusion | Manufacture an impasse: edge case, novel transfer, "predict before I show you"; raise difficulty; **withhold** worked examples (expertise reversal) |
| **Suspected gaming** (fast + verbatim) | Verify, don't advance | Immediate transfer Q on same concept; lower confidence |
| **Advancement** | Mastery gate | Advance unit only on a passed production/transfer check — never on one fast correct answer or self-reported confidence |

The control signal driving difficulty is the **rolling pass rate**, targeting ~80–85% (the
85% Rule), with per-item quality in the ZPD 0.5–0.7 band. Help moves **one rung at a time** up
on a miss and **fades one rung down** on success (Contingent Tutoring), never pinning to
extremes.

This is implemented as: `select_check_mode` (already exists) extended with the gaming/impasse
inputs and pass-rate, plus tutor-prompt fragments per move.

---

## 7. Measurement — how we actually tune it ("if we can measure it, we can optimize it")

The reason most tutors never improve is they have no ground truth. Ours:

- **Offline move evals (deepeval slow lane).** Scenario fixtures assert the policy produces
  the *right move*: does it withhold the answer when it should? produce a transfer (not
  lookup) question? detect a gaming transcript? escalate/fade help correctly? These are
  deterministic-enough to gate prompt changes in CI's slow lane.
- **Judge-calibration evals.** A labeled set of (answer, true-score) pairs; measure the
  judge's agreement/calibration. This protects the most load-bearing component.
- **The north-star metric: delayed retrieval performance.** Not in-session satisfaction —
  performance on *spaced* re-tests of a concept days later. This is the only metric that
  resists the fluency illusion. The SRS already surfaces due items; instrument their
  pass-rate as the outcome signal.
- **A/B-able prompt policies.** Because moves are selected by an explicit policy, we can
  version policies and compare delayed-retrieval outcomes — the loop that lets quality
  compound.

Build the measurement *before* heavy tuning. You cannot optimize toward a target you can't see.

---

## 8. Should we train our own model? (verdict + the real path)

**Short answer: not now — and never "train from scratch." But there is a real, defensible
custom-model endgame, and it is gated on the work above.**

- **Train a base model from scratch:** out of scope, permanently. Millions in compute, worse
  than Claude/GPT, no reason to compete at the layer where we have no advantage.
- **Fine-tune an open model *today*:** premature and likely a regression. We have (a) no
  training data, (b) no eval to optimize against, and (c) haven't exhausted prompting +
  orchestration, where most of the gain still lives. A fine-tuned 8B local model also *loses*
  the reasoning quality that the judge and misconception-detection depend on. It would freeze
  us onto a model that frontier releases lap within months.
- **The real endgame (the actual moat):** the interaction loop generates the one dataset
  nobody else has — *(learner state, tutor move, delayed-retrieval outcome)* tuples. That is
  ground truth about which pedagogical move *caused learning* for which learner state. Once
  we have (1) the eval harness (§7), (2) real usage data, and (3) prompting demonstrably
  maxed out, the high-value, in-scope move is **distilling the narrow components** — first the
  **judge** (cheap, runs every turn, narrow task), then possibly the **move-policy** — onto a
  small fine-tuned model trained on *our* outcome-labeled data. That is a moat (data others
  can't get), cost-positive, and local-first-friendly. Tutoring generation itself probably
  stays on a frontier model far longer.

So: the path to "our own AI" runs *through* the orchestration and measurement work, not around
it. Doing that work is also exactly what makes the product better than anything out there —
and it's the same investment whether or not we ever fine-tune.

**Order of operations:** instrument the loop → build evals → max out prompting → collect
outcome data → *then* distill the judge. Skipping to fine-tuning is the classic premature-
optimization trap.

---

## 9. Implementation roadmap (proposed v0.7.0 re-scope)

Phase 1 (done): per-unit difficulty + `select_check_mode` skeleton.

- **Phase 2 — Signals & the judge.** Harden the judge (misconception + `gameable` output);
  add pure-logic gaming detection (latency + n-gram overlap with shown text); add rolling
  pass-rate to the learner model. Logic + data + tests. *This is the measurement foundation;
  it must precede tuning the moves.*
- **Phase 3 — Policy in the prompt.** Encode the §6 state→move policy into `select_check_mode`
  (consume gaming/impasse/pass-rate) and tutor-prompt fragments: ungameable question rules,
  elicit-before-tell, impasse probes for mastering, attempt-gated help, mastery-gated
  advancement.
- **Phase 4 — Eval loop.** Move-evals + judge-calibration evals in the deepeval slow lane;
  instrument delayed-retrieval pass-rate as the north-star outcome.
- **Phase 5 (later) — `/difficulty` visibility & override** (the earlier Phase-2 draft,
  deprioritized below the pedagogy core).
- **Future (gated on Phase 4 + usage data):** distill the judge onto a small fine-tuned model
  trained on outcome-labeled data.

---

## 10. Most valuable sources (condensed)

The full set with findings/implications is in [LEARNING_SCIENCE.md](LEARNING_SCIENCE.md).
These are the load-bearing few for this design:

- **Chi, Siler, Jeong, Yamauchi & Hausmann (2001)**, *Learning from Human Tutoring*, Cognitive
  Science 25:471–533 — students learned as much when tutors were barred from explaining →
  **elicit, don't tell.**
- **VanLehn, Siler, Murray, Yamauchi & Baggett (2003)**, *Why Do Only Some Events Cause
  Learning During Human Tutoring?*, Cognition & Instruction 21(3):209–249 — learning needs an
  **impasse**; explanations only land after the learner is stuck.
- **Baker, Corbett & Koedinger (2004)**, *Detecting Student Misuse of Intelligent Tutoring
  Systems*, ITS 2004 (and Aleven & Koedinger 2000 on help abuse) — **gamers learn ~⅔ as
  much**; the core threat to design against.
- **Slamecka & Graf (1978)**, *The Generation Effect*, JEP:HLM 4(6):592–604 — **produced** info
  beats **read** info → ungameable, production-format checks.
- **Wilson, Shenhav, Straccia & Cohen (2019)**, *The Eighty Five Percent Rule for Optimal
  Learning*, Nature Communications 10:4646 — learning rate peaks at **~85% success** → the
  difficulty controller's target.
- **Bjork & Bjork (2011)**, *Making Things Hard on Yourself, but in a Good Way* — **desirable
  difficulties** and the **fluency illusion**: don't optimize for the learner feeling smooth.
- **Wood, Bruner & Ross (1976)**, *The Role of Tutoring in Problem Solving*, J. Child Psychol.
  Psychiatry 17:89–100 — **contingent scaffolding**: step help up/down by one, then fade it.
- **VanLehn (2011)**, *The Relative Effectiveness of Human/Intelligent Tutoring*, Educational
  Psychologist 46(4):197–221 — realistic effect sizes (human ≈ d 0.79); keeps us honest about
  what "great tutoring" actually buys.
