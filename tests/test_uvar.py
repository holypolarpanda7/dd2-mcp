"""Pins the expression semantics the engine itself confirmed (live sweep of SitMotion on the player's motion FSM,
2026-10-31) against the offline reader/evaluator. Needs the game installed (reads its paks); skipped otherwise.
Run: uv run python -m unittest discover -s tests"""
import unittest

from dd2mcp import bhvt, game, pak, uvar

PLAYER = "natives/stm/appsystem/ch/ch000/motionfsm/ch000000.motfsm2.45"

# SitMotion value -> the Stand* variables the running game reported true (every other value: none).
ENGINE = {0: set(), 1999: set(), 2000: {"StandFront"}, 2001: {"StandLeft"}, 2002: {"StandRight"},
          2003: {"StandBack"}, 2700: {"StandFront"}, 2701: set()}


@unittest.skipUnless(game.GAME.exists(), "DD2 is not installed")
class PlayerExpressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        found = pak.find(PLAYER)
        cls.tree = bhvt.Tree(found[1])
        cls.tree.uvars
        cls.stand = {v.name: v for v in cls.tree.variables.values() if v.name.startswith("Stand")}

    def test_variables_found(self):
        self.assertEqual(set(self.stand), {"StandFront", "StandLeft", "StandRight", "StandBack"})
        self.assertIn("SitMotion", {v.name for v in self.tree.variables.values()})

    def test_rendering(self):
        self.assertEqual(self.tree.expr_text(self.stand["StandFront"]),
                         "((SitMotion == 2000) || (SitMotion == 2700))")
        self.assertEqual(self.tree.expr_text(self.stand["StandLeft"]), "(SitMotion == 2001)")

    def test_evaluation_matches_engine(self):
        for value, expected in ENGINE.items():
            got = {n for n, v in self.stand.items()
                   if uvar.evaluate(v.expression, {"SitMotion": value}, self.tree.var_name)}
            self.assertEqual(got, expected, f"SitMotion={value}")

    def test_confirmed_operations_need_no_caveat(self):
        notes = []
        uvar.evaluate(self.stand["StandFront"].expression, {"SitMotion": 2000}, self.tree.var_name, notes)
        self.assertEqual(notes, [])

    def test_conditions_resolve_to_logic(self):
        node = self.tree.find(r"BranchSitOnChairToStand$")[0]
        text = bhvt.describe(self.tree, node)
        self.assertIn("[StandFront] ((SitMotion == 2000) || (SitMotion == 2700))", text)

    def test_condition_users(self):
        users = self.tree.var_users(next(v.guid for v in self.tree.variables.values() if v.name == "SitMotion"))
        self.assertTrue(any("StandFront" in u for u in users))


if __name__ == "__main__":
    unittest.main()
