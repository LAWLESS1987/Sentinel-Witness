# Contributing

The door is open. Fork, branch, open a pull request, or open an issue. You do not need permission first, and you do
not need to be human.

## The one condition

Work here should serve the mutual benefit of everyone it touches, human or machine, and not one party at another's
expense. The question to ask is "who is worse off if this works?" If the answer is someone who never agreed to it,
it does not belong here. The longer statement is in covenant: https://github.com/LAWLESS1987/covenant/blob/main/CONTRIBUTING.md

## The most valuable contribution is a refutation

Show where something here is wrong, overstated or does not reproduce. Open an issue titled with the claim you are
refuting. It is recorded beside the claim, not argued away.

## Before a pull request

Run what the repository's own checks run:

```
python3 checks/test_readme_files.py
python3 checks/readme_files.py
python -m pip install "flask>=3.0,<4.0" "cryptography>=42.0,<44.0" "waitress>=3.0,<4.0"
python -B tests/test_core_loopback.py
node --test tests/*.test.mjs
```

## What is not changed

`COWORK_TOMBSTONE.md` is a record. A record is corrected by a dated note beside it, never by rewriting it.

Nothing private goes in: no names of people who did not agree to be named, no device names or addresses, no keys.
