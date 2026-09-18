# Juridiq publication triage skill

Copy this entire folder to another Claude Code project at:

```text
.claude/skills/juridiq-publication-triage/
```

`SKILL.md` is the entry point. Keep every file in `references/`; they contain
the classification rules, task-model snapshot, PJe enrichment guidance, and
orchestration contract used by the skill.

For Claude Desktop, add `SKILL.md` and all files under `references/` to the
target Project's knowledge, then start a new chat in that Project.
