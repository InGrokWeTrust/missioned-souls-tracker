"""Fixed-budget, easy-to-audit YouTube discovery query coverage tests."""
import unittest
from discovery_search_plan import REACTION_KEYWORDS, discovery_queries


class SearchPlanTests(unittest.TestCase):
    def test_broad_query_on_every_run_and_rotating_plain_terms(self):
        self.assertEqual(REACTION_KEYWORDS, ("reaction", "react", "reactor", "reacting"))
        for slot, word in enumerate(REACTION_KEYWORDS):
            with self.subTest(slot=slot):
                self.assertEqual(list(discovery_queries(
                    "Missioned Souls", 2, rotation_slot=slot)), [
                    ('"Missioned Souls"', 0),
                    (f'"Missioned Souls" {word}', 0),
                ])

    def test_variations_repeat_after_one_cycle(self):
        self.assertEqual(
            list(discovery_queries("Missioned Souls", 2, rotation_slot=4)),
            list(discovery_queries("Missioned Souls", 2, rotation_slot=0)),
        )
        self.assertEqual(
            list(discovery_queries("Missioned Souls", 2, rotation_slot=5)),
            list(discovery_queries("Missioned Souls", 2, rotation_slot=1)),
        )

    def test_budget_stays_capped(self):
        for budget in range(1, 10):
            searches = list(discovery_queries("Missioned Souls", budget, rotation_slot=2))
            self.assertEqual(len(searches), min(budget, 5))
            self.assertEqual(searches[0], ('"Missioned Souls"', 0))
            self.assertEqual(len({q for q, _ in searches}), len(searches))
            self.assertTrue(all(page == 0 for _, page in searches))

    def test_one_search_is_always_broad(self):
        self.assertEqual(list(discovery_queries(
            "Missioned Souls", 1, rotation_slot=3)), [('"Missioned Souls"', 0)])

    def test_no_boolean_query_or_negative_operators(self):
        for slot in range(4):
            for query, page in discovery_queries("Missioned Souls", 2, rotation_slot=slot):
                self.assertNotIn("|", query)
                self.assertNotIn(" -", query)
                self.assertEqual(page, 0)

    def test_blank_subject_refused(self):
        with self.assertRaises(ValueError):
            list(discovery_queries(" ", 2))


if __name__ == "__main__":
    unittest.main()
