import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ftja.pipeline_view import read_config, write_config


class ProfileSourceTests(unittest.TestCase):
    def test_pipeline_registry_preserves_folder_sources(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "portfolio"
            folder.mkdir()
            write_config(tmp, profile_sources=[{
                "path": str(folder), "label": "My portfolio", "kind": "folder"
            }])
            sources = read_config(tmp)["profile_sources"]
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["kind"], "folder")
            self.assertTrue(sources[0]["exists"])

    def test_public_demo_uses_the_canonical_landing_source(self):
        root = Path(__file__).parents[1]
        landing = root.joinpath("landing.html").read_text(encoding="utf-8")
        self.assertFalse(root.joinpath("index.html").exists())
        for text in ["Review your profile summary.", "Confirm profile summary", "Fine-tune your job search.", "Suggested job titles", "Published within", "Confirm search settings"]:
            self.assertIn(text, landing)
        for text in ["Start with what you know.", "Confirm titles", "Posting age", "PROFILE SEARCH DRAFT CONFIRMED"]:
            self.assertNotIn(text, landing)


if __name__ == "__main__":
    unittest.main()
