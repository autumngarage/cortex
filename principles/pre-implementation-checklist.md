# Pre-Implementation Checklist

Before implementing any fix or feature, answer these questions. If you can't answer yes to all of them, stop and discuss scope.

## 1. Does shared infrastructure already solve this?

Search the project's existing shared layers (utilities, base classes, common modules) before writing new infrastructure. If a subsystem hand-rolls something the shared layer already provides, the fix is migration — not more hand-rolling.

## 2. Am I fixing the root cause or the symptom?

If the answer is "symptom," say so explicitly: *"This patches the symptom. The root cause is X and fixing it properly would require Y. Which do you want?"* Sometimes patching the symptom is the right call (time pressure, risk, scope). But it should be a conscious choice, not an accident.

## 3. Will this create a second code path?

If yes, can you delete the old one in the same PR? If not, you're adding tech debt — flag it. Two code paths that do "almost the same thing" are a maintenance trap: they drift apart silently and bugs in one don't surface until production.

## 4. Does this change touch a file with hand-rolled infrastructure?

If yes, the default action is migrating to the shared infrastructure, not patching the local copy. A patch on hand-rolled code deepens the debt. Migration eliminates it.
