"""Direct tests for pure tutor answer assessment."""

import unittest

from openlearn import answer_assessment


class AnswerAssessmentTests(unittest.TestCase):
    def test_overlap_normalizes_case_and_punctuation(self) -> None:
        self.assertEqual(
            answer_assessment.answer_tokens("Normal-mode, EDITS!"),
            ["normal", "mode", "edits"],
        )
        self.assertEqual(
            answer_assessment.trigram_jaccard("One two three four", "ONE, TWO three five"),
            1 / 3,
        )
        self.assertEqual(answer_assessment.trigram_jaccard("one two", "one two"), 0.0)

    def test_gaming_requires_production_and_six_answer_tokens(self) -> None:
        shown = "alpha beta gamma delta epsilon zeta"
        self.assertEqual(
            answer_assessment.detect_gaming_suspected(shown, shown, "production", False),
            (True, 1.0, 6),
        )
        self.assertEqual(
            answer_assessment.detect_gaming_suspected(
                "alpha beta gamma delta epsilon", shown, "production", False
            ),
            (False, 0.75, 5),
        )
        self.assertEqual(
            answer_assessment.detect_gaming_suspected(shown, shown, "recognition", False),
            (False, 1.0, 6),
        )

    def test_gameable_flag_is_independent_of_overlap(self) -> None:
        self.assertEqual(
            answer_assessment.detect_gaming_suspected("yes", "no", "recognition", True),
            (True, 0.0, 1),
        )
        self.assertFalse(answer_assessment.judge_gameable(1))
        self.assertFalse(answer_assessment.answer_eval_is_transfer("true"))
        self.assertTrue(answer_assessment.judge_gameable(True))
        self.assertTrue(answer_assessment.answer_eval_is_transfer(True))

    def test_answer_kind_defaults_to_production(self) -> None:
        self.assertEqual(answer_assessment.normalized_answer_kind("recognition"), "recognition")
        self.assertEqual(answer_assessment.normalized_answer_kind("Production"), "production")
        self.assertEqual(answer_assessment.normalized_answer_kind(None), "production")

    def test_cli_keeps_existing_helper_imports(self) -> None:
        from openlearn import cli

        for name in (
            "answer_tokens",
            "token_trigrams",
            "trigram_jaccard",
            "normalized_answer_kind",
            "answer_eval_is_transfer",
            "judge_gameable",
            "detect_gaming_suspected",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(cli, name), getattr(answer_assessment, name))
