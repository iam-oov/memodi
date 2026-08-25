# Dual Adversarial Review on memodi

**Status**: concept plan — implementation pending user confirmation, after the
gentle-ai/engram ecosystem is uninstalled from this machine.

Clean-room design. The concept is inspired by gentle-ai's "judgment day"
protocol (Apache-2.0), but no text, prompts, file contents, or naming are
reused — only the underlying idea, written from scratch for memodi.

## The idea

Code review by a single model inherits that model's blind spots. Instead:

1. An orchestrator confirms the review target (diff, files, feature, PR) and
   launches **two independent reviewer agents in parallel**, blind to each
   other, with identical scope and criteria. The orchestrator never reviews
   the code itself.
2. Findings are synthesized by **consensus**:
   - Both reviewers report the same issue → **confirmed**
   - Only one reports it → **suspect** (triaged with the user, never auto-fixed)
   - Reviewers contradict each other → **escalated** to the human
3. Severity distinguishes issues reachable through normal intended use from
   contrived/theoretical paths — only the former can block approval.
4. On user approval, a **fixer agent** applies confirmed findings only, with
   the minimal diff, touching nothing unflagged.
5. After any fix, **both reviewers re-run in parallel** before any terminal
   state. Terminal states are only **approved** or **escalated**; after two
   fix rounds with open issues, the user decides whether to continue.

## What memodi adds

This is where the reimplementation is more than a port — the review loop
becomes memory-aware:

- **Context in**: reviewers query prior decisions, conventions, and known
  gotchas for the workspace (`memodi_search_hybrid` →
  `memodi_get_observation`) before judging, so findings respect established
  project rules instead of generic taste. Memory is optional context: an
  unregistered path (`not_started`) degrades to a plain review, never blocks.
- **Context out**: the fixer saves one `bugfix` observation per confirmed fix
  (root cause included), and the orchestrator saves the final verdict as an
  observation under a per-review topic key — future reviews of the same area
  see past verdicts.
- **Explicit scoping**: every memodi call carries the workspace `path`, per
  memodi's auth model; the orchestrator injects it into each agent prompt.

## Deliberately not carried over

- gentle-ai's skill-registry/resolver machinery and its reporting fields
- Fixed model assignment tables (agents pin their own model, or inherit)
- Multi-adapter delegation variants — Claude Code named agents only
- The "judgment day" naming and output wording

## Implementation sketch

Three agent definitions plus one skill, all written from scratch:

| Piece | Role |
|---|---|
| `dual-review` skill | Orchestration contract: scope, parallel launch, consensus rules, fix gate, re-review loop, output format |
| reviewer agent ×2 | Read-only adversarial reviewers; tools: Read, Glob, Grep, Bash + memodi search/get |
| fixer agent | Edit-capable, confirmed-findings-only; tools above + Edit, Write + memodi save |

**Decided (2026-08-14): shipped in the memodi plugin as a product feature** —
every memodi user gets dual review. Target layout:

```
plugin/claude-code/
├── agents/dual-reviewer-a.md
├── agents/dual-reviewer-b.md
├── agents/dual-fixer.md
└── skills/dual-review/SKILL.md
```

Same quality bar as the rest of the plugin: docs updated (CLAUDE.md plugin
structure, README), and a `plugin.json` version bump in the release — plugin
updates are version-gated, so shipping without the bump means users never
receive it.

## Verification

Run a dual review on a small real diff in a memodi-registered workspace and
check: both reviewers launch in parallel, consensus buckets render, the fixer
only touches confirmed findings, re-review runs before the terminal state,
and the bugfix/verdict observations land in memodi.
