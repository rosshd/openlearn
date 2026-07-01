# Learning Science Reference

This file maps learning-science ideas to openLearn product behavior.
Use `.claude/skills/openlearn-tutor-policy/` for implementation rules.

## Product Rules

| Principle | Product behavior |
| --- | --- |
| Retrieval practice | Ask learners to produce answers, not just reread explanations |
| Spacing | Revisit concepts after delay through SRS and cumulative review |
| Interleaving | Mix recent concepts instead of drilling one pattern forever |
| Generation effect | Prefer free response, prediction, transformation, and hands-on checks |
| Self-explanation | Ask learners to explain why, compare, trace, or justify |
| Cognitive load | Narrow struggling turns to one concept and one action |
| Worked examples | Use after an attempt or when a learner is stuck, then fade |
| Expertise reversal | Give advanced learners harder transfer and less explicit scaffolding |
| ICAP | Favor constructive and interactive learner work over passive reading |
| Feedback quality | Give specific gap and next action, not only right or wrong |
| Productive failure | Let the learner try before revealing the solution when safe |
| Calibration | Compare confidence or fluency against actual answer quality |
| ZPD | Keep challenge hard enough to reveal gaps but not so hard it stalls |
| 85 percent rule | Aim for roughly 80-85 percent success over time |
| Desirable difficulties | Add effortful retrieval and transfer to prevent false fluency |
| Impasse-driven learning | Explanations land best after the learner hits a real obstacle |
| Help abuse research | Prevent answer extraction and copy-forward progress |
| Contingent tutoring | Step help up after misses and fade it after success |
| Mastery learning | Advance on durable evidence, not seat time or confidence |

## Implementation Implications

- Store per-concept attempts, outcomes, misconceptions, due dates, and mastery evidence.
- Track rolling pass rate and use it to tune difficulty.
- Prefer production and transfer questions for mastery gates.
- Withhold full answers until a genuine attempt when possible.
- Treat fast high-overlap answers as suspect.
- Use delayed retrieval performance as the best quality signal.
- Keep feedback short, specific, and actionable.

## Source Anchors

| Area | Sources |
| --- | --- |
| Retrieval practice | Roediger and Karpicke; Karpicke and Roediger |
| Spacing | Cepeda et al.; Pavlik and Anderson |
| Interleaving | Kornell and Bjork; Rohrer |
| Generation and self-explanation | Slamecka and Graf; Chi et al. |
| Cognitive load and worked examples | Sweller; Renkl; Kalyuga |
| Tutoring and impasse | Chi et al.; VanLehn et al. |
| Feedback | Hattie and Timperley; Shute |
| Productive failure | Kapur |
| Calibration and fluency | Bjork and Bjork |
| Optimal challenge | Wilson et al. |
| Gaming and help abuse | Baker, Corbett, and Koedinger; Aleven and Koedinger |
| Scaffolding | Wood, Bruner, and Ross |
| Mastery learning | Bloom |

## Synthesis

The tutor should choose moves from learner state:

- Below target: reduce load and scaffold.
- Near target: keep retrieval productive and varied.
- Above target: add transfer, edge cases, and desirable difficulty.
- Suspect answer: verify before advancing.
- Mastery candidate: require delayed or novel production evidence.
