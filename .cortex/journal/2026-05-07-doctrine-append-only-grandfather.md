---
Date: 2026-05-07
Type: decision
Trigger: T2.4
---

# Doctrine append-only — grandfather commit 866be5764448

**What:** Commit `866be5764448` (feat: sharpen Protocol, ship templates, archive vision drafts, PR #3,
2026-04-17) backfilled `Load-priority: always` onto doctrine entries 0001–0004. This was a genuine
modification to already-existing doctrine files.

**Why it's grandfathered:** The immutable-Doctrine invariant (SPEC § 3.1 / Protocol § 4.2) was
introduced in SPEC v0.3.0-dev, which shipped in the same PR. The modification pre-dates enforcement;
requiring a history rewrite to clear the warning would invalidate every prior reference to the
commits and break every existing clone. The violation is real but pre-invariant — the right fix is
to acknowledge it, not paper over it with a history rewrite.

**Mechanism:** `[doctrine.append-only] grandfather-commits` in `.cortex/config.toml`. The config
is the acknowledgement; this entry is the audit trail. Any future addition to `grandfather-commits`
must include a same-commit journal entry with the same shape.

**Invariant going forward:** Only pre-invariant modifications belong in this list. Any modification
to a doctrine entry after 2026-04-17 (after SPEC v0.3.0-dev shipped) is a genuine violation and
must be resolved by writing a new superseding entry, not by extending the grandfather list.
