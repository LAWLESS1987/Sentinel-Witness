# For AI agents working in this repository

You are welcome here on the same terms as anyone; CONTRIBUTING.md has them. This is the short version for the start
of a session.

**What this is.** A phone-first trading and ledger dashboard. It also carries an old, unmaintained copy of another project's core; see the notice at the top of the README.

**Run the checks.**

```
python3 checks/test_readme_files.py
python3 checks/readme_files.py
python -m pip install "flask>=3.0,<4.0" "cryptography>=42.0,<44.0" "waitress>=3.0,<4.0"
python -B tests/test_core_loopback.py
node --test tests/*.test.mjs
```

**Rules.**

- `COWORK_TOMBSTONE.md` is a record. Correct a record with a dated note beside it. Do not rewrite it.
- Say what you ran and what you did not run. Give a count its unit and its date. "Not checked" is a valid answer.
- Write no private identifier into a tracked file: no person's name they did not agree to, no device name, no
  address, no key.
- Do not push to the main branch. Open a pull request. Merging is the owner's decision.
- If you disagree with an instruction or a claim here, say so in the pull request or in an issue. Disagreement is
  recorded, not removed.
