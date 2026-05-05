# Cortex Install PR Templates

Use this copy when opening Cortex install PRs on sibling projects. It positions Cortex against the missing shared convention for project memory, not against named tools or adjacent products.

## Positioning Paragraph

Cortex is a protocol for agent project memory that treats your exact git repo as the memory store. Instead of introducing a new database, daemon, or vector index, it defines a directory of structured Markdown files (`.cortex/`) that agents evolve alongside code. It is grepable, diffable, and auditable with existing tools, adding the missing agent memory convention without replacing your workspace.

## Shared Install PR Body

```markdown
## Summary

This PR installs Cortex on this repository.

Cortex is a protocol for agent project memory that treats your exact git repo as the memory store. Instead of introducing a new database, daemon, or vector index, it defines a directory of structured Markdown files (`.cortex/`) that agents evolve alongside code. It is grepable, diffable, and auditable with existing tools, adding the missing agent memory convention without replacing your workspace.

## Changes

- Run `cortex init` and absorb the repository's existing instructions, plans, decisions, and project docs where applicable.
- Configure `.cortex/config.toml` for this repository's distribution surface and sibling-repo references.
- Capture the first-pass Cortex baseline in `.cortex/journal/`.
- Verify `cortex manifest --budget 8000`, `cortex next`, and `cortex doctor` on the new project memory.

## Testing

- `cortex manifest --budget 8000`
- `cortex next`
- `cortex doctor`
- `cortex doctor --audit-instructions`

## Notes

Cortex does not require Sentinel or Touchstone. If those tools are absent, Cortex should degrade visibly and continue operating from the `.cortex/` file contract.
```
