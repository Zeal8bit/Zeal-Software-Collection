"""Catalog invariants required for installing individual projects in fresh ZDE."""
from pathlib import Path
import sys
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/scripts"))
from build_community_image import ordered


class CollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "collection.yml"
        cls.entries = yaml.safe_load(path.read_text())["dependencies"]
        cls.by_id = {entry["id"]: entry for entry in cls.entries}

    def test_dependency_graph_is_complete_and_acyclic(self):
        self.assertEqual(len(self.entries), len(self.by_id), "Duplicate collection IDs")
        graph = {key: {"parents": entry.get("depends_on", [])}
                 for key, entry in self.by_id.items() if entry.get("zde") is not False}
        order = ordered(graph, list(graph))
        positions = {dep_id: i for i, dep_id in enumerate(order)}
        for dep_id, entry in graph.items():
            for parent in entry["parents"]:
                self.assertNotEqual(parent, "Zeal8bit/Zeal-8-bit-OS")
                self.assertLess(positions[parent], positions[dep_id])

    def test_microbe_resolves_all_build_prerequisites(self):
        graph = {key: {"parents": entry.get("depends_on", [])} for key, entry in self.by_id.items()}
        chain = ordered(graph, ["zoul0813/zeal-microbe"])
        self.assertEqual(chain[-1], "zoul0813/zeal-microbe")
        self.assertNotIn("Zeal8bit/Zeal-8-bit-OS", chain)
        self.assertTrue({"Zeal8bit/Zeal-VideoBoard-SDK",
                         "zoul0813/zeal-game-dev-kit", "zoul0813/zeal-coreutils",
                         "zoul0813/zeal-archiver"}.issubset(chain))


if __name__ == "__main__":
    unittest.main()
