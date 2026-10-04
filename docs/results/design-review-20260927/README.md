# Review evidence — 2026-09-27

These are the offline supporting checks for [the design review](../../design-review-20260927.md), frozen at source commit `3b6e2d7f790c0bc5000d5698e5d5d332e62945d7`. They cover attempts through 11, not attempt 12. The JSON files and test output preserve the original review results.

- `bundle-verification.json`: nine evidence archive hashes and file counts.
- `verified-evidence.json`: event/hash/replay checks, exact worker-request hashes, run configurations and outcomes.
- `reproduced-gaps.json`: two offline fixture reproductions at the reviewed source revision.
- `offline-tests.txt`: 92 passing tests at the reviewed revision.
- `verify_evidence.py` and `reproduce_gaps.py`: offline checks; no model calls.

To reproduce against the reviewed implementation, use a separate local copy at the source commit above and copy this review directory into the same relative path. Obtain the nine named archives and checksum files from evidence branch commit `3d5e925e413b92391c4d9d989beea58a56daac69`. Verify each archive against `bundle-verification.json`, then safely extract regular files beneath `evidence/<archive-name-without-.tar.gz>/` in this directory, retaining each archive's own top-level directory. Never restore these historical ledgers over the active project ledger. Run the scripts from the repository root with Python 3.11 or newer. They write their local JSON outputs beside themselves. Keep extracted evidence private and untracked.

The request-boundary verification checks request hashes and research identifiers. Its empty secret list does not perform a fresh credential-value scan; it is not evidence of a comprehensive credential audit. The active project's normal boundary audit can perform that additional check without printing credentials.

Later evidence, including attempt 12, is on the private `evidence/containment-run-s7-20260927` branch. This supporting snapshot is deliberately not relabeled as reviewing it.
