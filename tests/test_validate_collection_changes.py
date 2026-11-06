import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/scripts"))
from validate_collection_changes import changed_entries, entries


class ChangedEntriesTests(unittest.TestCase):
    def test_added_changed_and_removed_entries(self):
        base = "dependencies:\n- {id: unchanged, repo: one}\n- {id: changed, repo: old}\n- {id: removed}\n"
        current = "dependencies:\n- {id: unchanged, repo: one}\n- {id: changed, repo: new}\n- {id: added}\n"
        self.assertEqual([e["id"] for e in changed_entries(base, current)], ["changed", "added"])

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            entries("dependencies:\n- {id: a}\n- {id: a}\n")


if __name__ == "__main__":
    unittest.main()
