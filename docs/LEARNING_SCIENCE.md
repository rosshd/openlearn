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

## The Optimal Challenge Point — the 85% Rule

**Source:** Wilson, Shenhav, Straccia & Cohen (2019), *The Eighty Five Percent Rule for
Optimal Learning*, Nature Communications 10:4646. https://doi.org/10.1038/s41467-019-12552-4

**Finding:** For a broad class of gradient-descent learners, the *rate* of learning is
maximized when training accuracy sits at ~85% (error rate ~15.87%) — neither too easy nor
too hard. Below this, items are too hard and gradients are noisy; above it, items are too
easy and carry little new information. This is a computational derivation, but it converges
with the human "desirable difficulty" and ZPD literature on the same conclusion: the fastest
progress per unit time happens at a measurable, intermediate success rate, not at 100%.

**Implication:** Make **success rate the primary control signal** for the difficulty engine,
targeting roughly 80–85% of checks passed over a rolling window. This is distinct from, and
complementary to, the existing ZPD `score` band (0.5–0.7) which rates the *quality of a single
answer*: success rate controls per-session difficulty; score rates per-item effort. If the
learner is passing >~90% of checks, the material is too easy — raise unit difficulty / shift
to application or impasse-style questions. If passing <~70%, reduce intrinsic load and add
scaffolding. A learner cruising at 100% correct is the clearest signal they are *not* in the
productive zone — and often the signal that they are pattern-matching the text rather than
understanding (see Gaming the System).

**Where to apply:** `adjust_unit_difficulty()` and `select_check_mode()`. Add a rolling
pass-rate signal alongside the per-answer `score`; drive difficulty toward the ~80–85% band.

---

## Desirable Difficulties

**Source:** Bjork & Bjork (2011), *Making Things Hard on Yourself, but in a Good Way:
Creating Desirable Difficulties to Enhance Learning*, in Gernsbacher et al. (eds.),
*Psychology and the Real World*, Worth Publishers, pp. 56–64. Foundational: Bjork (1994).

**Finding:** Conditions that *slow* acquisition and *feel* harder (spacing, interleaving,
retrieval, variation, reduced feedback) produce stronger long-term retention and transfer.
Conversely, conditions that make performance feel fluent and fast during study (re-reading,
massed practice, immediate answers) create an **illusion of competence** that collapses on
delayed tests. Performance-during-learning is a poor and often inverse proxy for learning.

**Implication:** This is the umbrella principle behind retrieval, spacing, interleaving, and
the generation effect (all below/above). For the tutor it means: do not optimize for the
learner *feeling* smooth. A learner who answers instantly by copying from what was just shown
is exhibiting the fluency illusion, not mastery. Introduce difficulty deliberately — delay
the worked example, vary the surface form of questions, and require production.

**Where to apply:** Cross-cutting design principle. Specifically gate the worked example
(`deep` check mode) and bias toward production-format questions in `check_mode_prompt()`.

---

## Impasse-Driven Learning

**Source:** VanLehn, Siler, Murray, Yamauchi & Baggett (2003), *Why Do Only Some Events
Cause Learning During Human Tutoring?*, Cognition and Instruction 21(3):209–249.

**Finding:** Across ~125 hours of expert human tutoring, learning events were overwhelmingly
tied to the student reaching an **impasse** — getting stuck, hitting a contradiction, or
recognizing a gap. When students were *not* at an impasse, tutorial explanations rarely
produced learning, no matter how good the explanation. Once at an impasse, explanations
(and the student's own resolution attempts) frequently did.

**Implication:** Explanations are wasted before the learner is stuck. The tutor should let
the learner attempt and *surface* an impasse before teaching — and for a learner who is not
confused, it should manufacture productive friction (a leading question, an edge case, a
"what would happen if…") rather than just confirming and moving on. This directly answers the
"leading questions for a non-confused learner" question: yes — for a learner who is *not*
struggling, a leading/probing question that exposes an unconsidered case is high-value because
it creates the impasse that makes the next explanation stick.

**Where to apply:** Wrong-answer flow (attempt before explanation, already partly done via
Productive Failure) AND the `mastering`/`on_track` path of `select_check_mode()` — these
learners should get application/transfer and edge-case probes, not acknowledgement.

---

## What Makes Tutoring Work: Student Construction over Telling

**Source:** Chi, Siler, Jeong, Yamauchi & Hausmann (2001), *Learning from Human Tutoring*,
Cognitive Science 25:471–533. Effect-size context: VanLehn (2011), *The Relative
Effectiveness of Human Tutoring, Intelligent Tutoring Systems, and Other Tutoring Systems*,
Educational Psychologist 46(4):197–221.

**Finding:** Chi et al. tested whether tutoring works because of what the *tutor* does
(explaining well) or what the *student* does (constructing understanding). When tutors were
**suppressed from giving explanations and feedback** — restricted to prompting — students
learned *just as much*. The gains came from student construction, not tutor exposition.
VanLehn's later meta-analysis tempers the famous numbers: human tutoring is ~d=0.79 over no
tutoring (and step-based ITS ~d=0.76), not the ~2 sigma once assumed (see Bloom below) — but
the mechanism is consistent: interaction that forces the student to generate.

**Implication:** The tutor's default move should be to *elicit*, not *tell*. Prefer "what do
you think happens next, and why?" over a polished explanation. Telling is a fallback for a
genuine impasse the learner cannot resolve with prompting — not the opening move. This is the
single strongest argument for a Socratic-leaning question policy.

**Where to apply:** `TUTOR_FORMAT_RULES` default stance; `check_mode_prompt()` should favor
elicitation; reserve direct exposition for the `deep` mode's post-attempt worked example.

---

## Generation Effect

**Source:** Slamecka & Graf (1978), *The Generation Effect: Delineation of a Phenomenon*,
Journal of Experimental Psychology: Human Learning and Memory 4(6):592–604. Meta-analysis:
Bertsch, Pesta, Wiscott & McDaniel (2007), Memory & Cognition 35(2).

**Finding:** Information the learner *generates* (completes, derives, produces) is remembered
substantially better than the identical information merely *read* — robust across recognition,
free recall, and cued recall. Producing the answer, even partially, beats being shown it.

**Implication:** This is the precise counter to the failure mode of concern — a learner who
"sees the material and pulls the answer from the text and moves on." Require the learner to
*produce* before the answer is ever visible: close the source, ask for the answer in their own
words, ask them to predict before revealing. A check that can be passed by copying from
on-screen text provides near-zero learning; questions should demand transformation
(paraphrase, apply to a new case, derive) rather than location-and-copy.

**Where to apply:** Question mechanics in `TUTOR_FORMAT_RULES` and `check_mode_prompt()`:
prefer questions that cannot be answered by quoting the just-shown text; require the learner
to produce/transform.

---

## Gaming the System & Help Abuse

**Source:** Baker, Corbett & Koedinger (2004), *Detecting Student Misuse of Intelligent
Tutoring Systems*, Proc. ITS 2004. Earlier on help abuse: Aleven & Koedinger (2000).

**Finding:** Students who "game the system" — systematically extracting answers by abusing
hints, feedback, or available text rather than reasoning — learn only about **two-thirds as
much** as comparable students who don't. Gaming is measurable from behavioral signals (very
fast responses, rapid hint-cycling, answers that track the visible text) and correlates
strongly with poor learning.

**Implication:** This names and quantifies exactly the behavior to design against. The tutor
should (a) **make answers ungameable** — production/transformation questions that the source
text doesn't contain verbatim (see Generation Effect); (b) **gate help behind an attempt**
(see Productive Failure) so hints can't be cycled for the answer; (c) **detect likely gaming**
— an unusually fast, near-verbatim "correct" answer should *lower* confidence in mastery and
trigger a transfer question on the same concept rather than advancement; (d) never let a
concept be marked known on a single fast correct answer (ties to Metacognition / Calibration).

**Where to apply:** Evaluation flow + difficulty engine. Treat suspiciously fast/verbatim
correct answers as weak evidence; require a transfer check before advancing. Pairs with the
calibration signal.

---

## Scaffolding & Contingent Tutoring

**Source:** Wood, Bruner & Ross (1976), *The Role of Tutoring in Problem Solving*, Journal of
Child Psychology and Psychiatry 17:89–100 (origin of "scaffolding"). Contingent-shift rule:
Wood, Wood & Middleton (1978).

**Finding:** Effective tutors provide support contingently and *fade* it: when the learner
struggles, give more specific help on the next move; when the learner succeeds, give less on
the next. The support is a temporary scaffold to be withdrawn as competence grows — keeping
the task itself within reach while transferring control to the learner.

**Implication:** This is the operational rule that ties the whole adaptive engine together.
Help level should move *contingently and by one step* with performance — not jump to full
explanation on a single miss, nor stay maximal once the learner recovers. The check-mode
ladder (acknowledge → recall → application → deep) is exactly such a scaffold; the engine
should step up one rung on a miss and *fade down* one rung on success, rather than pinning to
extremes. This is the design intent behind `adjust_unit_difficulty()` moving ±1.

**Where to apply:** `select_check_mode()` + `adjust_unit_difficulty()`: enforce single-step,
contingent moves and active fading of scaffolds as the rolling pass-rate rises.

---

## Bloom's 2 Sigma & Mastery Learning

**Source:** Bloom (1984), *The 2 Sigma Problem: The Search for Methods of Group Instruction
as Effective as One-to-One Tutoring*, Educational Researcher 13(6):4–16.

**Finding:** Bloom reported that students under one-to-one tutoring *with mastery learning*
(don't advance until the current objective is met) performed ~2 standard deviations above
conventional classroom students — i.e., the average tutored student beat ~98% of the class.
The headline 2-sigma figure has not replicated at that magnitude (VanLehn 2011 puts realistic
tutoring nearer d≈0.8), but the two design levers Bloom identified — **individualization** and
**mastery gating** — remain well supported.

**Implication:** openlearn's premise (a personal one-to-one tutor) is the right lever; the
realistic target is a large-but-not-magical gain, achieved through individualized difficulty
and mastery gating. Do not advance a unit on shallow or single-shot evidence — require
demonstrated mastery (a passed transfer/production check), consistent with calibration and
anti-gaming above.

**Where to apply:** Unit-advancement logic; mastery gate before `current_unit` increments.

---

## Test-Enhanced Learning: Quiz Frequency, Placement & Stakes

**Source:** McDaniel, Agarwal, Huelser, McDermott & Roediger (2011), *Test-Enhanced Learning
in a Middle School Science Classroom: The Effects of Quiz Frequency and Placement*, Journal of
Educational Psychology 103(2). Roediger, Agarwal, McDaniel & McDermott (2011), *Test-Enhanced
Learning in the Classroom: Long-Term Improvements From Quizzing*, JEP: Applied 17(4). Transfer:
McDaniel, Thomas, Agarwal, McDermott & Roediger (2013), *Quizzing in Middle-School Science:
Successful Transfer Performance on Classroom Exams*, Applied Cognitive Psychology 27. Stakes/
anxiety: Khanna (2015), *Ungraded Pop Quizzes*, Teaching of Psychology 42(2); Roediger, Putnam
& Smith (2011), *Ten Benefits of Testing*, Psychology of Learning and Motivation 55.

**Finding:** Frequent **low-stakes quizzing with feedback** during a course produces durable
gains on later summative exams — and the benefit *persists to cumulative semester and
end-of-year exams*, not just immediate retests. Crucially, McDaniel et al. (2013) found the
gains include **transfer** to new questions, not only verbatim repeats. On the design
variables the question "pop quiz?" raises:
- **Stakes:** ungraded/low-stakes quizzes match or beat graded ones for learning, and
  ungraded quizzes produce markedly less anxiety. Students *like* having them when ungraded.
- **Surprise (announced vs. unannounced):** the learning benefit comes from the **retrieval
  practice**, not the surprise. Unannounced ("pop") quizzes add panic, stress, and anxiety;
  announced/expected assessments yield less anxiety, more enjoyment, and equal-or-better
  performance. Surprise is a cost, not a mechanism.
- **Placement/frequency:** distributing quizzes through learning (and making later quizzes
  **cumulative**) leverages spacing and interleaving; cumulative quizzing beats one-shot
  end-of-chapter testing.
- **Indirect benefit:** quizzing improves the learner's *metacognitive calibration* — it
  reveals what they don't know, redirecting study (ties to Metacognition & Calibration).

**Implication for openlearn:** Do **not** build surprise/high-stakes "pop" quizzes — for a
self-directed learner there is no grade anyway, so the only thing "surprise" adds is anxiety
with no learning upside. Instead: keep all checks low-stakes/ungraded by framing (which the
growth-mindset feedback rules already support), and add **frequent, cumulative, spaced
retrieval quizzes interspersed through learning** rather than only an end-of-chapter quiz. The
quiz trigger should be driven by *accumulated material and spacing* (and SRS due-density), not
solely by a chapter boundary. Cumulative quizzes should pull interleaved items across recently
studied units (see Interleaving) and include transfer questions (see Generation Effect), which
also makes them the natural mechanism for the unit-level mastery check and the delayed-
retrieval north-star metric.

**Profile fit (Mastery Profiles, V0.7.0 Contract 4):**
- `efficient` → shorter, mostly recent-material checks in test-like format; light cumulative.
- `proficient` → cumulative spaced quizzes with transfer questions.
- `deep` → more frequent, heavily interleaved cumulative quizzes with explain-back.

**Where to apply:** A "cumulative check" mechanic distinct from the chapter quiz, triggered by
material volume + spacing; the unit-level mastery check (Contract 3); the eval loop's delayed-
retrieval outcome signal (Phase 4).

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
| Target ~80–85% rolling pass rate as difficulty controller | 85% Rule, ZPD, desirable difficulties |
| Leading/edge-case probes for non-confused learners | Impasse-driven learning, desirable difficulties |
| Elicit before telling; prompt rather than explain | Student construction (Chi 2001) |
| Questions require production/transform, not text lookup | Generation effect |
| Attempt-gated help; distrust fast verbatim "correct" answers | Gaming the system / help abuse |
| Step help up/down by one rung, fade scaffolds on success | Contingent tutoring / scaffolding |
| Mastery gate before advancing a unit | Bloom 2 sigma / mastery learning |
| Frequent low-stakes cumulative quizzes, not surprise/graded pop quizzes | Test-enhanced learning |

---

## Adaptive Strategy Selection (synthesis)

The research above does not point to one "best" tutoring style — it points to a *policy*
that selects the move from the learner's current state. The unifying goal is to hold the
learner in the productive zone (~80–85% success, answers requiring genuine production) and to
prevent the two failure modes: **shutdown** (too hard, <~70% success) and **shallow fluency**
(too easy or text-lookup, >~90% effortless success). The signals openlearn already tracks —
rolling pass rate, per-answer `score`, consecutive correct/misses, response latency, and
calibration — are sufficient to drive this.

Recommended state → move policy (the basis for tuning `select_check_mode` and the tutor prompt):

- **Struggling** (misses ≥2, or score <0.35, or pass rate <~70%): reduce intrinsic load —
  one sub-concept at a time (Cognitive Load); attempt first, then a worked example (Productive
  Failure, Worked Examples); contingent, specific help faded as they recover (Scaffolding).
  Avoid pure recall-of-text; keep it producible but small.
- **On track** (in the ~0.5–0.7 score / ~80–85% pass band): this is the target — keep them
  here. Elicit rather than tell (Chi 2001); production/transfer questions (Generation Effect);
  "why" and "what if" probes (Elaborative Interrogation). Hold difficulty steady.
- **Mastering** (≥3 correct, score ≥0.8, pass rate >~90%): the danger zone for shallow
  fluency. Do **not** just acknowledge and advance. Manufacture a productive impasse — an edge
  case, a transfer to a novel context, "predict before I show you" (Impasse-Driven Learning,
  Desirable Difficulties). Raise unit difficulty. Worked examples now *hurt* (Expertise
  Reversal) — withhold them. This is where leading questions for a *non-confused* learner pay
  off most.
- **Any tier, suspected gaming** (very fast, near-verbatim correct answer): treat as weak
  evidence — do not advance; pose an immediate transfer question on the same concept and lower
  confidence in that concept (Gaming the System, Calibration).
- **Advancement** is always mastery-gated: a concept/unit advances only on a passed
  production/transfer check, never on a single fast correct answer or self-reported confidence
  (Bloom mastery, Calibration).

Answering Ross's framing questions directly:
1. *Should leading questions be used even when the learner isn't confused?* Yes — for the
   mastering/on-track learner this is the highest-value move, because it manufactures the
   impasse that makes learning happen (VanLehn 2003) and counters fluency illusions (Bjork).
2. *How do we stop "see material → copy answer → move on"?* Make checks ungameable by design
   (production/transform, source closed — Generation Effect), gate help behind an attempt, and
   distrust fast verbatim correct answers (Gaming the System). Comprehension is demonstrated by
   transfer to a new case, not by reproduction of the just-seen text.
3. *Stay in the sweet spot of challenge?* Drive difficulty toward an ~80–85% rolling pass rate
   (85% Rule) with per-item answer quality in the 0.5–0.7 band (ZPD) — the productive struggle
   zone where progress per unit time is maximized.
