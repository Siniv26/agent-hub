---
name: Task
about: One task on the multi-agent Issue bus
title: "[task] "
labels: "status:todo"
assignees: ""
---

## Goal

What done looks like, in one or two lines.

## Owner agent

Partner | DSH | Codex | Hermes

## Inputs

Links, files, constraints. No secrets.

## Done-when

- [ ] Acceptance check

## Risk

`none`, or name the approval required (delete, external message, spend, irreversible).

## Notes

Handoff context. If Owner is DSH and a coding step is required, say whether to call `subagent_codex` once.

Suggested labels — add one agent and one type; leave `status:todo` until work starts:

- agent: `agent:partner` `agent:dsh` `agent:codex` `agent:hermes`
- type: `type:analyze` `type:implement` `type:ops` `type:smoke`
- status: `status:todo` `status:doing` `status:blocked` `status:done`

Routing: `docs/ROUTING.md`.

The Windows worker does not claim this longer body. For an automatic run, use Bus task, Bus analyze, or Bus implement. Those bodies are only Goal, Task, and Risk. Risk `none` can auto-run when that lane is on. Risk `approval` or `high` waits for a human. `agent:dsh` + `type:implement` is the same worker implement lane as `agent:codex`. `agent:dsh` + `type:analyze` is not automatic.
