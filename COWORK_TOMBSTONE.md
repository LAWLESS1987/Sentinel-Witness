# TOMBSTONE: cowork/
**Status:** detached, local-only since 2026-10-03 (not deleted; nothing removed)
**GitHub copies:** purged. The branch history that held the folder was deleted (privacy first); content lives only on the owner's machine.

The Claude ⇄ ChatGPT cowork folder (handoffs, decisions, `seal.py` tombstone/restore system) was moved off GitHub to keep it private.
This marker records exactly what was sealed so the local copy can prove it's unchanged when it reattaches.

## Record
- 2026-10-03 SWITCHED PRIVATE: cowork/ moved off GitHub to refine privately. Owner chose privacy over keeping the
  folder's history on GitHub. Nothing was removed from the folder itself; its own TOMBSTONES.log and .tombstones/
  keep every version locally. The hashes below are the public proof of what it contained at this moment.

## Seal at detachment
MANIFEST.json sha3-256: `25fcaa423e4296ee3aba0b5740eaeefda50321b51b12d610dc32e48c60897a68`

```json
{
  "files": {
    "LOG.md": "dd93336c87595a58cdbd65c997d40a90c9a13229c188ce3a3522afe040c2713c",
    "README.md": "a7d9169ff60bcc4d7660ac6b04e32ca3e3758cd84d2983b75748d073f1cde64d",
    "TEMPLATE.md": "95e07d3aecfd14e0a179f63189695130b00584be9bfee54c8bdce0dfe0ea15f9",
    "decisions/.gitkeep": "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
    "decisions/2026-10-03-add-only.md": "711415e84209cda0a3ee06df3e5973d599c578797bf7640a063e5fd4970ad3e6",
    "from_chatgpt/.gitkeep": "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
    "seal.py": "be147e266b860967ee6292c03edefe19c3c7ef972c12d3730f269b8b9ccbd53a",
    "shared/.gitkeep": "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
    "to_chatgpt/.gitkeep": "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a"
  }
}
```

## Reattach when ready
1. In your local copy run `python cowork/seal.py verify`. It should say OK, or list exactly what changed since detachment.
2. Compare `openssl dgst -sha3-256 cowork/MANIFEST.json` to the hash above. A match means nothing drifted.
3. Remove the `cowork/` line from `.gitignore`, then `git add cowork` and commit.
4. Add a line to this file: `REATTACHED <date> manifest=<hash>`. Keep this file; it's the record.
