import unittest
from discovery_search_plan import discovery_queries

class SearchPlanTests(unittest.TestCase):
    def test_default_broad_coverage_two_pages(self):
        self.assertEqual(list(discovery_queries("Missioned Souls", 2, targeted=False)), [
            ('"Missioned Souls"', 0),
            ('"Missioned Souls"', 1),
        ])

    def test_targeted_coverage_replaces_not_adds_request(self):
        self.assertEqual(list(discovery_queries("Missioned Souls", 2, targeted=True)), [
            ('"Missioned Souls"', 0),
            ('"Missioned Souls" reaction', 0),
        ])

    def test_budget_always_capped_even_if_config_is_high(self):
        for targeted in (False, True):
            self.assertEqual(len(list(discovery_queries("Missioned Souls", 999, targeted=targeted))), 5)

    def test_single_page_never_adds_search(self):
        self.assertEqual(list(discovery_queries("Missioned Souls", 1, targeted=True)),
                         [('"Missioned Souls"', 0)])

if __name__ == "__main__":
    unittest.main()
