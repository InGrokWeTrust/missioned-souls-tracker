"""Offline regression tests for the original bounded broad search pages."""
import unittest
from discovery_search_plan import discovery_queries


class SearchPlanTests(unittest.TestCase):
    def test_original_query_uses_two_pages_of_quoted_subject(self):
        self.assertEqual(list(discovery_queries("Missioned Souls", 2)), [
            ('"Missioned Souls"', 0),
            ('"Missioned Souls"', 1),
        ])

    def test_budget_always_capped(self):
        for budget in range(1, 10):
            with self.subTest(budget=budget):
                searches = list(discovery_queries("Missioned Souls", budget))
                self.assertEqual(len(searches), min(budget, 5))
                self.assertEqual([page for _, page in searches],
                                 list(range(min(budget, 5))))
                self.assertTrue(all(query == '"Missioned Souls"'
                                    for query, _ in searches))

    def test_one_search_never_adds_a_second_page(self):
        self.assertEqual(list(discovery_queries("Missioned Souls", 1)),
                         [('"Missioned Souls"', 0)])

    def test_no_reaction_keywords_or_boolean_query_operators(self):
        for query, _ in discovery_queries("Missioned Souls", 5):
            self.assertNotIn(" reaction", query)
            self.assertNotIn(" react", query)
            self.assertNotIn(" reactor", query)
            self.assertNotIn(" reacting", query)
            self.assertNotIn("|", query)
            self.assertNotIn(" -", query)

    def test_blank_subject_refused(self):
        with self.assertRaises(ValueError):
            list(discovery_queries(" ", 2))


if __name__ == "__main__":
    unittest.main()
