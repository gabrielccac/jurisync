# Juridiq publication triage skill

Copy this entire folder to another Claude Code project at:

```text
.claude/skills/juridiq-publication-triage/
```

`SKILL.md` is the orchestration entry point. Its references contain only the
classification rules and PJe enrichment guidance; account-scoped IDs are
discovered from the live Juridiq connector.

For Claude Desktop, add `SKILL.md` and all files under `references/` to the
target Project's knowledge, then start a new chat in that Project.
