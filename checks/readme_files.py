#!/usr/bin/env python3
"""readme_files.py -- does README.md name files this branch actually has?

  python checks/readme_files.py        exit 0 when the README and the branch agree, 1 with one line per finding

Measured 2026-11-13 on `main`: the README named 15 files that are not in this branch (the app's source, its tests,
its build configuration) and told the reader to run `npm test`, with no package.json to run it. A reader could not
tell what was here from what was described.

The rule now: every file the README names in backticks is in this branch, OR it is listed in
checks/NOT_IN_THIS_BRANCH.txt, which the README's own "What is in this branch" section points to. The list must be
exact both ways: a named file that is missing and not listed is a finding, and so is a listed file that is no longer
named or has since been added (the list is then out of date). Every Python file must also compile.

Reads only. Standard library only. It cannot tell whether what the README says ABOUT a file is true.
"""
import os
import re
import sys

NAMED = re.compile(r"`([A-Za-z0-9_./-]+\.(?:js|jsx|ts|tsx|py|json|png|md|txt|yml|yaml|html|css))`")
LIST = os.path.join("checks", "NOT_IN_THIS_BRANCH.txt")


def tracked(root):
    """Every file under root (not .git), as forward-slash relative paths."""
    out = set()
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__")]
        for name in files:
            out.add(os.path.relpath(os.path.join(folder, name), root).replace("\\", "/"))
    return out


def has(name, files):
    """A README names a file by its path or by its bare name; either counts when exactly that path or name is here."""
    name = name.lstrip("./")
    return name in files or ("/" not in name and any(f.rsplit("/", 1)[-1] == name for f in files))


def check(root="."):
    findings = []
    try:
        with open(os.path.join(root, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
    except OSError:
        return ["README.md could not be read"]
    files = tracked(root)
    named = sorted(set(NAMED.findall(readme)))
    absent = sorted(n for n in named if not has(n, files))
    try:
        with open(os.path.join(root, LIST), encoding="utf-8") as fh:
            listed = sorted({ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")})
    except OSError:
        listed = []
        if absent:
            findings.append("%s is missing, and README.md names files that are not here" % LIST.replace("\\", "/"))
    for n in absent:
        if n not in listed:
            findings.append("README.md names %s, which is not in this branch and not in the list of what is not here" % n)
    for n in listed:
        if n not in named:
            findings.append("%s lists %s, which README.md no longer names" % (LIST.replace("\\", "/"), n))
        elif has(n, files):
            findings.append("%s lists %s, which is in this branch now: take it off the list" % (LIST.replace("\\", "/"), n))
    if listed and LIST.replace("\\", "/") not in readme:
        findings.append("README.md does not point to %s, so a reader cannot tell what is not here" % LIST.replace("\\", "/"))
    for rel in sorted(files):
        if rel.endswith(".py"):
            try:
                with open(os.path.join(root, rel), encoding="utf-8-sig") as fh:     # a leading byte-order mark is valid Python
                    compile(fh.read(), rel, "exec")
            except (SyntaxError, ValueError, UnicodeDecodeError) as e:
                findings.append("%s does not compile (%s)" % (rel, type(e).__name__))
    return findings


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    findings = check(root)
    for line in findings:
        print("FINDING  " + line)
    print("%d finding(s)" % len(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
