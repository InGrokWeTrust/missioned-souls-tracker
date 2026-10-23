import unittest
from discovery_search_plan import discovery_queries

class SearchPlanTests(unittest.TestCase):
    def test_default_bounded_plan(self):
        self.assertEqual(list(discovery_queries("Missioned Souls", 2)), [
            ('"Missioned Souls"', 0),
            ('"Missioned Souls"', 1),
            ('"Missioned Souls" reaction', 0),
        ])

    def test_budget_is_capped(self):
        self.assertEqual(len(list(discovery_queries("Missioned Souls", 999))), 6)

    def test_minimum_broad_page(self):
        self.assertEqual(len(list(discovery_queries("Missioned Souls", 0))), 2)

if __name__ == "__main__":
    unittest.main()
