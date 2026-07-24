import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openlearn import course_templates


class CourseTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.resource_patch = mock.patch.object(
            course_templates,
            "template_resources",
            return_value=self.root,
        )
        self.resource_patch.start()

    def tearDown(self) -> None:
        self.resource_patch.stop()
        self.temp_dir.cleanup()

    def write_template(self, filename: str = "python-basics.json", **overrides: object) -> Path:
        data = {
            "name": "Python Basics",
            "slug": "python-basics",
            "goal": "Write fundamental Python programs",
            "tags": ["programming", "beginner"],
            "units": ["Unit 1: Values", "Unit 2: Functions"],
        }
        data.update(overrides)
        path = self.root / filename
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_loads_valid_template_as_immutable_values(self) -> None:
        self.write_template()

        template = course_templates.load_course_template("python-basics")

        self.assertEqual(template.slug, "python-basics")
        self.assertEqual(template.tags, ("programming", "beginner"))
        self.assertEqual(template.units, ("Unit 1: Values", "Unit 2: Functions"))

    def test_rejects_template_id_path_traversal(self) -> None:
        with self.assertRaisesRegex(
            course_templates.CourseTemplateNotFoundError,
            "invalid template ID",
        ):
            course_templates.load_course_template("../private")

    def test_rejects_filename_slug_mismatch(self) -> None:
        self.write_template(slug="python")

        with self.assertRaisesRegex(
            course_templates.CourseTemplateError,
            "slug must match its filename",
        ):
            course_templates.load_course_template("python-basics")

    def test_rejects_missing_unknown_and_malformed_fields(self) -> None:
        cases = [
            ({"tags": None}, "tags must be a non-empty string list"),
            ({"units": []}, "units must be a non-empty string list"),
            ({"name": " Python Basics"}, "name must be a trimmed non-empty string"),
            ({"extra": "value"}, "unexpected extra"),
        ]
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                self.write_template(**overrides)
                with self.assertRaisesRegex(course_templates.CourseTemplateError, expected):
                    course_templates.load_course_template("python-basics")

    def test_lists_templates_in_stable_filename_order(self) -> None:
        self.write_template()
        self.write_template(
            filename="git.json",
            name="Git",
            slug="git",
            goal="Use Git confidently",
            tags=["tools"],
            units=["Unit 1: Commits"],
        )

        templates = course_templates.available_course_templates()

        self.assertEqual([template.slug for template in templates], ["git", "python-basics"])


if __name__ == "__main__":
    unittest.main()
