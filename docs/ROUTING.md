# Routing / 路由

GitHub Issues are the task bus. One Issue is one task. Labels say who owns it, what kind of work it is, and where it stands.

Issues 是任务总线。一条 Issue 一件事。标签标明负责人、工作类型和状态。

## Partner (Grok Bot) / 协调

Triage. Create and update Issues. Decide who runs. Phone and remote coordination.

Partner does not take implementation that belongs to Codex, and does not spend a DSH turn on engineering.

## DSH / 分析

DeepSeek Harness (`deepseek-flash`).

Analysis, summarization, planning, cheap classification, draft Issue bodies. Prefer DSH for high-volume, low-risk thinking.

Inside one DSH turn, DSH may call `subagent_codex` once when a coding step is required. That call is the handoff. Do not start a second implementation in parallel unless Partner says so.

## Codex / 实现

`gpt-6.1-sol` via the native CLI bridge.

Code changes, debugging, tests, PR-quality engineering. Prefer Codex for implementation.

## Hermes / 本地执行

Stub only. Local Windows execution / ACP comes later. Do not route live work here until Partner marks Hermes ready.

## Who runs / 派工

| Signal | Route | Labels |
| --- | --- | --- |
| Unclear scope, plan, summary, or a draft Issue | DSH | `agent:dsh` `type:analyze` |
| Code, tests, debugging, PR-quality change | Codex | `agent:codex` `type:implement` |
| Triage, status, handoff, phone or remote | Partner | `agent:partner` |
| Local Windows or ACP execution (later) | Hermes | `agent:hermes` `type:ops` |
| Bus connectivity check | DSH, then one Codex call | `type:smoke` — [SMOKE.md](SMOKE.md) |

Partner sets **Owner agent** and moves status: `status:todo` → `status:doing` → `status:done`, or `status:blocked`.

## Approval / 审批

Risky actions need explicit user approval before anyone runs them:

- delete (files, branches, Issues, data)
- send external messages
- spend money
- anything irreversible

Drafting text and opening a PR for review are normal work.

## Labels / 标签

One `agent:*`, one `type:*`, and one `status:*`.

**Agent**

- `agent:partner` — Partner / Grok Bot
- `agent:dsh` — DeepSeek Harness
- `agent:codex` — Codex
- `agent:hermes` — Hermes

**Type**

- `type:analyze` — analysis, summary, plan, classification
- `type:implement` — code, tests, debugging
- `type:ops` — local or operational execution
- `type:smoke` — bus connectivity check

**Status**

- `status:todo` — not started
- `status:doing` — in progress
- `status:blocked` — waiting on approval, input, or a missing agent
- `status:done` — finished

`type:ops` and `status:blocked` are part of this scheme. Create them on the repo if they are missing. The other labels above are already present.

## Issue body / 议题字段

- **Goal** — done, in one or two lines
- **Owner agent** — Partner, DSH, Codex, or Hermes
- **Inputs** — links, files, constraints. No secrets.
- **Done-when** — checkable acceptance
- **Risk** — `none`, or the approval required
- **Notes** — handoff context, whether DSH should call `subagent_codex`

Template: [../.github/ISSUE_TEMPLATE/task.md](../.github/ISSUE_TEMPLATE/task.md)
