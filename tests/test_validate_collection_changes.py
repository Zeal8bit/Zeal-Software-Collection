import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/scripts"))
from validate_collection_changes import changed_entries, entries, validation_targets


class ChangedEntriesTests(unittest.TestCase):
    def test_added_changed_and_removed_entries(self):
        base = "dependencies:\n- {id: unchanged, repo: one}\n- {id: changed, repo: old}\n- {id: removed}\n"
        current = "dependencies:\n- {id: unchanged, repo: one}\n- {id: changed, repo: new}\n- {id: added}\n"
        self.assertEqual([e["id"] for e in changed_entries(base, current)], ["changed", "added"])

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            entries("dependencies:\n- {id: a}\n- {id: a}\n")

    def test_kernel_owners_validate_when_romdisk_dependencies_change(self):
        base = '''dependencies:
- id: Zeal8bit/Zeal-8-bit-OS
  build: false
  kernels: [{config: zeal8bit, output: zeal8bit.rom}]
- id: shell
  build: false
  kernels: [{config: shell, output: shell.img, romdisk: [utils]}]
- {id: utils, repo: old}
'''
        self.assertEqual(validation_targets(base, base.replace("repo: old", "repo: new")), ["shell", "utils"])
        self.assertEqual(validation_targets(base, base.replace("zeal8bit.rom", "stock.img")),
                         ["Zeal8bit/Zeal-8-bit-OS", "shell"])


if __name__ == "__main__":
    unittest.main()
