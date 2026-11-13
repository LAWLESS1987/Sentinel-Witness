#!/usr/bin/env python3
"""Tests of checks/readme_files.py, both ways, on a small branch built in a temporary folder."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import readme_files  # noqa: E402


class Branch(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="readme_files_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.files = {
            "README.md": "The app: `Dashboard.jsx` and `lib/limits.js`. Not here: `tradeGate.js`, `src/App.jsx` "
                         "(see checks/NOT_IN_THIS_BRANCH.txt). A word like `npm test` or `--flag` is not a file.\n",
            "Dashboard.jsx": "export default 1\n", "lib/limits.js": "export const x = 1\n", "bridge.py": "print('ok')\n",
            "checks/NOT_IN_THIS_BRANCH.txt": "# named in README.md, not in this branch\ntradeGate.js\nsrc/App.jsx\n",
        }

    def findings(self):
        shutil.rmtree(self.root)
        for rel, text in self.files.items():
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        return readme_files.check(self.root)

    def test_a_branch_whose_readme_and_list_agree_has_no_finding(self):
        self.assertEqual(self.findings(), [])

    def test_a_leading_dot_slash_is_the_same_name_in_the_readme_and_in_the_list(self):
        listed = "# named in README.md, not in this branch\n./tradeGate.js\nsrc/App.jsx\n"
        self.files["checks/NOT_IN_THIS_BRANCH.txt"] = listed
        self.assertEqual(self.findings(), [])                                  # ./ in the list, plain in the README
        self.files["README.md"] = self.files["README.md"].replace("`tradeGate.js`", "`./tradeGate.js`")
        self.assertEqual(self.findings(), [])                                  # ./ in both
        self.files["checks/NOT_IN_THIS_BRANCH.txt"] = listed.replace("./tradeGate.js", "tradeGate.js")
        self.assertEqual(self.findings(), [])                                  # ./ in the README only
        self.files["checks/NOT_IN_THIS_BRANCH.txt"] = listed.replace("./tradeGate.js\n", "./tradeGate.js\n./gone.js\n")
        self.assertEqual(len(self.findings()), 1)                              # a ./ entry nobody names is still stale
        self.assertIn("gone.js, which README.md no longer names", self.findings()[0])
        self.files["checks/NOT_IN_THIS_BRANCH.txt"] = listed
        self.files["tradeGate.js"] = "export default 1\n"
        self.assertEqual(len(self.findings()), 1)                              # and one that is here now is still stale
        self.assertIn("which is in this branch now", self.findings()[0])

    def test_a_bare_name_is_found_anywhere_and_a_path_only_at_that_path(self):
        self.files["README.md"] = "Uses `limits.js` and `lib/limits.js`.\n"
        del self.files["checks/NOT_IN_THIS_BRANCH.txt"]
        self.assertEqual(self.findings(), [])
        self.files["README.md"] = "Uses `src/limits.js`.\n"
        self.assertEqual(self.findings(), ["checks/NOT_IN_THIS_BRANCH.txt is missing, and README.md names files that are not here",
                                           "README.md names src/limits.js, which is not in this branch and not in the list of what is not here"])

    def test_a_name_written_with_a_leading_dot_slash_is_the_same_name(self):
        self.files["README.md"] += "It imports `./tradeGate.js` and `./lib/limits.js`.\n"
        self.assertEqual(self.findings(), [])
        self.files["README.md"] += "And `./ledger.js`.\n"
        self.assertEqual(self.findings(), ["README.md names ledger.js, which is not in this branch and not in the list of what is not here"])

    def test_a_named_file_that_is_missing_and_not_listed(self):
        self.files["README.md"] += "Also `ledger.js`.\n"
        self.assertEqual(self.findings(), ["README.md names ledger.js, which is not in this branch and not in the list of what is not here"])

    def test_a_listed_file_the_readme_no_longer_names(self):
        self.files["checks/NOT_IN_THIS_BRANCH.txt"] += "gridMath.js\n"
        self.assertEqual(self.findings(), ["checks/NOT_IN_THIS_BRANCH.txt lists gridMath.js, which README.md no longer names"])

    def test_a_listed_file_that_has_since_been_added(self):
        self.files["tradeGate.js"] = "export const gate = 1\n"
        self.assertEqual(self.findings(), ["checks/NOT_IN_THIS_BRANCH.txt lists tradeGate.js, which is in this branch now: take it off the list"])

    def test_a_readme_that_does_not_point_to_the_list(self):
        self.files["README.md"] = self.files["README.md"].replace("(see checks/NOT_IN_THIS_BRANCH.txt)", "")
        self.assertEqual(self.findings(), ["README.md does not point to checks/NOT_IN_THIS_BRANCH.txt, so a reader cannot tell what is not here"])

    def test_a_python_file_that_does_not_compile(self):
        self.files["bridge.py"] = "def f(:\n"
        self.assertEqual(self.findings(), ["bridge.py does not compile (SyntaxError)"])

    def test_no_readme(self):
        del self.files["README.md"]
        self.assertEqual(self.findings(), ["README.md could not be read"])

    def test_this_branch_agrees_with_its_readme(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.path.exists(os.path.join(root, "README.md")):
            self.assertEqual(readme_files.check(root), [])


if __name__ == "__main__":
    unittest.main()
