import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_support import model_matches, search_model_name


class CatalogueMatchingTests(unittest.TestCase):
    def test_se_third_generation_explicit(self):
        self.assertTrue(model_matches("Apple iPhone SE (3rd Generation), 64GB", "iPhone SE 3rd Generation"))

    def test_se_second_generation_year(self):
        self.assertTrue(model_matches("Apple iPhone SE 2020 64GB", "iPhone SE 2nd Generation"))

    def test_se_third_generation_year(self):
        self.assertTrue(model_matches("Apple iPhone SE 2022 128GB", "iPhone SE 3rd Generation"))

    def test_ambiguous_se_rejected(self):
        self.assertFalse(model_matches("Apple iPhone SE 64GB", "iPhone SE 3rd Generation"))

    def test_wrong_se_generation_rejected(self):
        self.assertFalse(model_matches("iPhone SE 2020 64GB", "iPhone SE 3rd Generation"))

    def test_conflicting_se_generation_rejected(self):
        self.assertFalse(model_matches("iPhone SE 3rd Generation 2020 64GB", "iPhone SE 3rd Generation"))

    def test_first_se_generation_rejected(self):
        self.assertFalse(model_matches("iPhone SE 2016", "iPhone SE 2nd Generation"))

    def test_promo_alias_does_not_match_pro(self):
        self.assertFalse(model_matches("Apple iPhone 13 Pro 128GB", "iPhone 13 - Christmas Deal!"))

    def test_promo_alias_matches_base_phone(self):
        self.assertTrue(model_matches("Apple iPhone 13 128GB", "iPhone 13 - Christmas Deal!"))
        self.assertEqual(search_model_name("iPhone 13 - Christmas Deal!"), "iPhone 13")

    def test_unknown_promo_not_silently_normalized(self):
        self.assertFalse(model_matches("iPhone 13", "iPhone 13 sale"))

    def test_model_suffix_is_exact(self):
        self.assertFalse(model_matches("iPhone XS Max 64GB", "iPhone XS"))
        self.assertTrue(model_matches("iPhone 13 Mini 128GB", "iPhone 13 Mini"))

    def test_multiple_phone_models_rejected(self):
        self.assertFalse(model_matches("iPhone 12 or iPhone 13", "iPhone 12"))


if __name__ == "__main__":
    unittest.main()
