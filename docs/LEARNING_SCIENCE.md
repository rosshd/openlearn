# Learning Science Reference

Research findings relevant to openlearn's tutor design. Each entry includes the core
finding, its implication for the tutor, and where in the codebase it should be applied.

---

## Retrieval Practice (Testing Effect)

**Source:** Roediger & Karpicke (2006), *Test-Enhanced Learning*, Science 319(5865).
Also: Karpicke & Blunt (2011), Dunlosky et al. (2013) meta-analysis.

**Finding:** Students who study via repeated testing recall 50%+ more after one week
compared to students who spend the same time re-reading. The act of retrieval — not
exposure — strengthens memory.

**Implication:** Active recall questions (quiz, free response, fill-in-the-blank) should
dominate over explanations. Every lesson should end with a retrieval attempt. Avoid letting
learners just read the response without being asked to produce something.

**Where to apply:** `TUTOR_FORMAT_RULES`, question mechanics section. The tutor should
default to asking for production (recall, explanation, trace) over recognition (multiple
choice) when the learner has seen the concept before.

---

## Spaced Repetition

**Source:** Ebbinghaus (1885) forgetting curve. Cepeda et al. (2006) meta-analysis of
254 studies. Wozniak & Gorzelanczyk (1994) SM-2 algorithm.

**Finding:** Memory decays exponentially without review, but each retrieval resets and
strengthens the trace. Optimal review intervals are longer than most learners intuitively
schedule — days to weeks, not hours.

**Implication:** The Ebisu SRS already implements this. The key gap is that learners
should be told *why* they're reviewing something: "You marked this hard 5 days ago" is
more motivating than an opaque review queue.

**Where to apply:** `cmd_review` output, resume context display. Show the forgetting-curve
rationale in the review prompt when surfacing a due item.

---

## Interleaving

**Source:** Rohrer, Dedrick & Stershic (2015), *Interleaved Practice Improves Mathematics
Learning*, Journal of Educational Psychology. Taylor & Rohrer (2010): interleaving beat
blocking by 43% on delayed tests.

**Finding:** Mixing different concept types within a session (interleaving) produces better
long-term retention than exhaustively finishing one concept before moving to the next
(blocking) — even though blocking feels more comfortable and produces better short-term
performance.

**Implication:** During review sessions, mix concepts from different units rather than
reviewing all Unit 1 concepts together. For long courses, occasionally revisit earlier
concepts mid-session ("quick flash review").

**Where to apply:** `cmd_review` and the due-item selection algorithm. When selecting due
items, prefer a mix across units over sequential unit ordering.

---

## Elaborative Interrogation

**Source:** Pressley et al. (1987). Dunlosky et al. (2013) rated it "moderate" utility
across all learner types and content domains.

**Finding:** Asking learners "why does this work?" or "why is this true?" during study
produces deeper encoding than "what is this?" questions. The generation of a causal
explanation forces integration with prior knowledge.

**Implication:** The tutor should regularly vary between recall ("what is X?") and
elaboration ("why does X work this way?" / "what would happen if you removed Y?").
Free-response questions should frequently be "why" framed.

**Where to apply:** `TUTOR_FORMAT_RULES` question mechanics. Add explicit guidance that
"why" and "what happens if" questions should be used alongside recall checks.

---

## Self-Explanation Effect

**Source:** Chi, de Leeuw, Chiu & LaVancher (1994), *Eliciting Self-Explanations Improves
Understanding*, Cognitive Science 18(3). Students who explained to themselves while reading
learned approximately 2× more than passive readers.

**Finding:** Generating an explanation — even an incomplete or incorrect one — forces the
learner to confront their own gaps. The act of explaining is a form of retrieval practice
with self-monitoring built in.

**Implication:** Before confirming an answer is wrong, ask the learner to "explain how you
got there." This forces metacognitive monitoring and often allows the learner to self-correct
before the tutor intervenes.

**Where to apply:** Wrong-answer feedback flow. When `verdict == "wrong"` and the answer
was not completely off-topic, the tutor's first move should be "walk me through your
reasoning" rather than immediately giving a hint or explanation.

---

## Cognitive Load Theory

**Source:** Sweller (1988, 2010), *Cognitive Load During Problem Solving*, Cognitive Science.

**Finding:** Working memory is limited to ~4 items. Learning is hindered by extraneous
cognitive load (poor presentation, irrelevant details) and by intrinsic load that exceeds
working memory capacity. Germane load — the effort of schema formation — is what produces
learning, and it should be protected.

Three types:
- **Intrinsic**: inherent complexity of the material (can't reduce without simplifying content)
- **Extraneous**: bad presentation (reduce this aggressively)
- **Germane**: productive effort of learning (maximize this)

**Implication:**
- Keep responses short and visually chunked (already enforced in TUTOR_FORMAT_RULES).
- Never teach more than one new concept per turn.
- When a learner is struggling (low rolling accuracy), reduce intrinsic load by breaking
  the concept into smaller sub-concepts before re-attempting the check.

**Where to apply:** Adaptive difficulty logic (v0.6.0 Phase 3). In "struggling" tier,
automatically cap each response to one concept and one follow-up question.

---

## Worked Examples & Expertise Reversal

**Source:** Sweller & Cooper (1985). Kalyuga (2007), *Expertise Reversal Effect*,
Educational Psychology Review.

**Finding:** For novices, studying worked examples is more effective than independent
problem solving — it avoids high cognitive load before the learner has a schema to build on.
However, as expertise grows, worked examples *reduce* learning (the expertise reversal
effect) — experts need challenge, not hand-holding.

**Implication:** The adaptive difficulty system should explicitly shift the worked-example/
problem-solving ratio based on learner tier:
- Struggling → offer worked example before the problem
- On track → brief worked example alongside a similar problem
- Mastering → problem only; worked example on request

**Where to apply:** v0.6.0 Phase 3 difficulty tier logic. Tag worked-example responses so
the tier system can decide when to include them.

---

## ICAP Framework

**Source:** Chi & Wylie (2014), *The ICAP Framework: Linking Cognitive Engagement to Active
Learning Outcomes*, Educational Psychologist 49(4).

**Finding:** Learning engagement exists on a hierarchy:
- **Interactive** (dialogue, debate, co-explanation) → highest learning gains
- **Constructive** (generating summaries, concept maps, predictions)
- **Active** (annotating, highlighting, manipulating)
- **Passive** (reading, watching, listening)

Each level produces measurably better outcomes than the one below it.

**Implication:** The REPL conversation is inherently *interactive* — the highest engagement
mode. This is openlearn's core differentiator. Features should protect and extend this:
- `/drill` → constructive + interactive (generate code, explain to tutor)
- Review sessions → interactive if framed as dialogue, not flashcard-flip
- Video suggestions → passive (lowest) — use as a supplement, never a replacement

**Where to apply:** Product positioning. When designing new features, prefer interactive
over constructive over passive delivery. Don't add a "watch-this-video" primary path.

---

## Feedback Quality: Elaborated vs. Simple

**Source:** Shute (2008), *Focus on Formative Feedback*, Review of Educational Research.
Butler & Roediger (2008) on delayed vs. immediate feedback.

**Finding:** Elaborated feedback (explaining *why* an answer is wrong and *what* the correct
reasoning is) significantly outperforms simple verification ("wrong") or correct-answer-only
feedback. The explanation creates an additional retrieval opportunity and corrects the
misconception directly.

Immediate feedback is best for factual recall. Slightly delayed feedback (even a few seconds
of hinting before correction) produces better transfer to new problems.

**Implication:**
- Always include an explanation in wrong-answer feedback. "That's not quite right" alone
  is nearly useless.
- The hint-first approach (v0.6.0 Phase 1) — ask a guiding question before revealing the
  answer — is better for transfer than immediate correction.
- Structured evaluation should always produce an `explanation` field, not just a verdict.

**Where to apply:** v0.6.0 Phase 1 structured evaluation. `TUTOR_FORMAT_RULES` feedback
behavior.

---

## Productive Failure

**Source:** Kapur (2016), *Examining Productive Failure, Productive Success, Unproductive
Failure, and Unproductive Success in Learning*, Educational Psychologist.

**Finding:** Learners who attempt a problem before receiving instruction — even if they
fail — develop better conceptual understanding than those who receive instruction first.
The key is that the failure must be "productive": the learner must engage genuinely, not
give up immediately.

**Implication:** For `/drill` and coding exercises, the tutor should not offer hints or
explain the solution until the learner has made at least one real attempt. The first
response to a stuck learner should be a guiding question, not a worked example.

**Where to apply:** `/drill` and `/check` feedback flow. Gate the worked example behind
at least one genuine attempt.

---

## Metacognition & Calibration

**Source:** Flavell (1979) metacognition framework. Dunning & Kruger (1999).
Koriat (1997) on the Feeling of Knowing.

**Finding:** Learners are systematically miscalibrated about their own knowledge — novices
overestimate and advanced learners often underestimate. Ease of processing (fluency) is
mistaken for understanding. Learners who say "I know this" may still fail retrieval.

**Implication:**
- Track a `confidence_vs_accuracy` signal: when a learner skips hints or says "I get it"
  but then answers incorrectly, flag the concept for extra review.
- Periodically surface calibration prompts: "Before I ask the question — how confident
  are you on this one? 1-5" and compare to actual result.
- Don't let the learner advance a concept solely on self-reported confidence.

**Where to apply:** Future per-concept difficulty tracking. Confidence collection is a
low-friction v0.6.0 addition to the check flow.

---

## Growth Mindset & Feedback Language

**Source:** Dweck (2006), *Mindset: The New Psychology of Success*. Mueller & Dweck
(1998), *Praise for Intelligence Can Undermine Children's Motivation and Performance*.

**Finding:** Praising effort and process ("great approach, let's refine it") produces more
resilient learners than praising ability ("you're smart at this"). After setbacks, process-
focused feedback leads to continued effort; ability-focused feedback leads to avoidance.

**Implication:** Tutor feedback language should be process-oriented:
- ✓ "Good reasoning — the logic is sound but you're missing one edge case."
- ✗ "Wrong."
- ✓ "You're making progress on this concept — let's try one more variation."
- ✗ "You keep getting this wrong."

**Where to apply:** `TUTOR_FORMAT_RULES` feedback section. Add explicit guidance on
language framing in wrong-answer responses.

---

## Zone of Proximal Development

**Source:** Vygotsky (1978), *Mind in Society*.

**Finding:** Learning is most efficient in the "zone of proximal development" — just
beyond what the learner can do independently, but within reach with guidance. Too easy
produces boredom; too hard produces shutdown.

**Implication:** The adaptive difficulty system should target the zone: problems that
require effort but are solvable with the hint system available. The structured evaluation
`score` field (0–1) is a direct proxy for ZPD alignment — target 0.5–0.7 as the sweet
spot.

**Where to apply:** Adaptive difficulty tier boundaries (v0.6.0 Phase 3). Target score
range, not just pass/fail.

---

## Application Summary

| Feature | Research basis |
|---------|---------------|
| Active recall checks after every lesson | Testing effect |
| Hint before answer on wrong response | Productive failure, elaborated feedback |
| "Walk me through your reasoning" prompt | Self-explanation effect |
| Mix concepts across units in review | Interleaving |
| Difficulty tier based on rolling accuracy | ZPD, expertise reversal |
| Worked example in "struggling" tier only | Worked examples effect |
| "Why does this work?" free-response questions | Elaborative interrogation |
| Process-oriented feedback language | Growth mindset |
| Structured visual formatting in responses | Cognitive load (extraneous reduction) |
| REPL conversation as primary mode | ICAP interactive tier |
| Confidence tracking vs. actual performance | Metacognition / calibration |
| Show review rationale ("you marked this hard 5 days ago") | Spaced repetition motivation |
