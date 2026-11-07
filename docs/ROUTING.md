# Routing / 路由

GitHub Issues are the task bus. One Issue is one task. Labels say who owns it, what kind of work it is, and where it stands.

Issues 是任务总线。一条 Issue 一件事。标签标明负责人、工作类型和状态。

## Partner (Grok Bot) / 协调

Triage. Create and update Issues. Decide who runs.

Partner leaves implementation to Codex. For analysis text alone, Partner uses Hermes oneshot. Use DSH when the turn needs the Harness UI or a `subagent_codex` call.

Phone/remote entry is deferred: Telegram gateway, and Tailscale tunnel to serve/dashboard. Do not treat either as ready.

## DSH / 编排

DeepSeek Harness (`deepseek-flash`).

DSH stays the path for the Harness UI and for `subagent_codex` orchestration. It can still analyze, summarize, plan, classify, and draft Issue bodies.

When Partner only needs analysis text, use Hermes oneshot. That CLI path is the cheaper one.

Inside one DSH turn, call `subagent_codex` once when a coding step is required. That call is the handoff. Do not start a second implementation in parallel unless Partner says so.

## Codex / 实现

Code changes, debugging, tests, PR-quality engineering. Prefer Codex for implementation (`gpt-6.1-sol`).

Two ways in, for a person at the machine:

- DSH calls `subagent_codex` inside one DSH turn
- native CLI at `%LOCALAPPDATA%\OpenAI\Codex\bin\14b838c1d074ff01\codex.exe` (0.167.0-alpha.3.2, not on PATH)

The public worker does not start Codex. `codex exec -s read-only` can still read the home directory and can still load MCP or user config, so it is not treated as an isolation boundary.

## Hermes / 分析（CLI）

Hermes 0.22.7 can run a DeepSeek oneshot on Windows. [Issue #4](https://github.com/Siniv26/agent-hub/issues/4) closed PASS with `HERMES_ANALYSIS_OK` for the binary itself.

The public Issue worker does not send Issue text to Hermes. An empty `-t` means every tool. `memory`, `file`, `terminal`, `browser`, and `delegation` are not allowed. `todo` plus `--safe-mode` still uses the real `HERMES_HOME`, and credentials are not copied into a fresh home, so that lane is deferred.

Binary: `%LOCALAPPDATA%\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\hermes\bin\hermes.exe`

`HERMES_HOME` is that `Local\hermes` directory (`...\LocalCache\Local\hermes`), not `~\.hermes`.

## Who runs / 派工

| Signal | Route | Labels |
| --- | --- | --- |
| Analysis text only | Hermes oneshot | `agent:hermes` `type:analyze` |
| Harness UI, or analysis that must call Codex | DSH | `agent:dsh` `type:analyze` |
| Code, tests, debugging, PR-quality change | Codex | `agent:codex` `type:implement` |
| Triage, status, handoff | Partner | `agent:partner` |
| DSH → Codex connectivity check | DSH, then one Codex call | `type:smoke` — [SMOKE.md](SMOKE.md) |

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

## Worker / 工人

v1 is default off and lives under `%LOCALAPPDATA%\AgentHub\worker`. It claims one opted-in smoke Issue (`bus:ready`, author `Siniv26`, `type:smoke`, `agent:partner`, risk `none`) and closes it only after `BUS_SMOKE_OK`. Runtime GitHub auth is `gh auth login` on that machine (gh 2.103.3 user scope; device code). The Cursor GitHub connector is not runtime auth.

Hermes, DSH, and Codex are recorded above and are not started. Operations: [OPS.zh.md](OPS.zh.md). Phone template: [../.github/ISSUE_TEMPLATE/bus-task.md](../.github/ISSUE_TEMPLATE/bus-task.md).
