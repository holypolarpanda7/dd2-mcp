import unittest
from unittest import mock

from dd2mcp import catalog, rsz


class ParentCycleTest(unittest.TestCase):
    """The IL2CPP subclass tree has self-parents; type_info once looped on them until WSL ran out of memory."""

    def info(self, parent, name):
        data = {"methods": {name: []}, "enums": {}, "subs": {}, "parent": parent}
        with mock.patch.object(catalog, "_build", return_value=data), mock.patch.object(rsz, "_index", return_value={}):
            return catalog.type_info(name)

    def test_self_parent_stops(self):
        out = self.info({"app.Shot": "via.Action", "via.Action": "via.Action"}, "app.Shot")
        self.assertIn("parents: via.Action\n", out + "\n")

    def test_longer_cycle_stops(self):
        out = self.info({"a.A": "a.B", "a.B": "a.C", "a.C": "a.B"}, "a.A")
        self.assertIn("parents: a.B > a.C\n", out + "\n")


if __name__ == "__main__":
    unittest.main()
