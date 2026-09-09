"""Configuration parsing tests — comma-separated env lists must work."""
import os
import unittest

from app.config import Settings


class TestSettingsParsing(unittest.TestCase):
    def test_comma_separated_allowed_origins(self):
        settings = Settings(
            _env_file=None,
            ALLOWED_ORIGINS="http://localhost:5173,http://localhost:3000",
            ALLOWED_IMAGE_TYPES="image/jpeg,image/png",
        )
        self.assertEqual(
            settings.allowed_origins,
            ["http://localhost:5173", "http://localhost:3000"],
        )
        self.assertEqual(settings.allowed_image_types, ["image/jpeg", "image/png"])

    def test_json_array_allowed_origins(self):
        settings = Settings(
            _env_file=None,
            ALLOWED_ORIGINS='["http://a.test", "http://b.test"]',
        )
        self.assertEqual(settings.allowed_origins, ["http://a.test", "http://b.test"])

    def test_default_origins_are_lists(self):
        self.assertIsInstance(Settings(_env_file=None).allowed_origins, list)


if __name__ == "__main__":
    unittest.main()
