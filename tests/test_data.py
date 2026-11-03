import unittest

from dd2mcp import msg, table


class MsgTest(unittest.TestCase):
    def test_item_name(self):
        m = msg.load("natives/stm/message/ui/itemname.msg.24")
        names = {e.name: e.text() for e in m.entries}
        self.assertEqual(names["item_name_77"], "Wakestone")


class TableTest(unittest.TestCase):
    P = "natives/stm/appsystem/item/itemdata/itemweapondata.user.3"

    def test_weapon_rows_sorted_and_named(self):
        out = table.render(self.P, cols="Name,_Id,_MagicAttack", sort="-_MagicAttack", limit=1)
        self.assertIn("Dragon's Wit", out)
        self.assertIn("440", out)

    def test_where_filter(self):
        out = table.render(self.P, cols="_Id", where="_Id=1606")
        self.assertIn("1/138 rows", out)

    def test_enum_decoration(self):
        self.assertIn("3:Equip", table.render(self.P, cols="_Category", limit=1))


if __name__ == "__main__":
    unittest.main()
