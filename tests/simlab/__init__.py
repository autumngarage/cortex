"""simlab — deterministic synthetic-project test environment (cortex#520-#522).

Test tooling, not product surface: simlab materializes complete fake repos
(real git history, instruction files, ADR chains, CODEOWNERS) from
declarative canonical-JSON scenario specs, runs the shipped derive →
local-store → fixture-pack review pipeline end to end against them with
recorded model responses, and seeds the standing demo tenant. Everything is
seeded and byte-stable: the same spec materializes to the same derive
``event_hash`` set on any machine, so the whole loop regression-tests
hermetically and demos run on rails.

Module map:

- ``specs``      — the versioned spec format (archetypes + scenarios)
- ``generator``  — spec → tmp repo with deterministic git history (#520)
- ``runner``     — scenario pipeline + expectation verification (#521)
- ``recordings`` — scripted evaluate responses, recorded and committed (#521)
- ``seed_demo``  — demo-tenant seeding over the merged push machinery (#522)
"""
