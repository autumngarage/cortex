# Documentation Ownership

Every volatile fact in the project's documentation must have exactly one canonical owner. Duplicating the same fact across multiple documents is a maintenance trap — when the fact changes, some copies get updated and others don't, creating contradictions that erode trust in all the docs.

## Rules

1. **One canonical owner per fact.** If a fact (URL, test count, file layout, config shape, deployment target) appears in documentation, one file owns it. Other files link to the owner rather than restating the fact.

2. **Avoid duplicating volatile information.** Volatile facts include: test counts, live URLs, route inventories, file-by-file layouts, config blocks, dependency versions, metric targets. If a volatile fact changes, update the canonical owner — not N summary docs.

3. **Each document has a clear scope.** The README owns the repo overview and quickstart. A setup doc owns operational setup. An architecture doc owns system design and package boundaries. A roadmap doc owns current phase and next steps. If a new doc is needed, define its scope before writing it.

4. **Config examples live in example files, not prose.** A `config.example.json` or `.env.example` is the canonical source of config shape. Documentation should point to the example file, not duplicate large config blocks that go stale.

5. **When in doubt, delete the duplicate.** If you find the same fact in two places and can't easily determine which is canonical, delete the less-authoritative copy and add a link to the other. One correct source is better than two potentially-wrong sources.
