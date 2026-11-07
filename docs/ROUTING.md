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

When Partner only needs analysis text, use Hermes oneshot. That CLI path is the cheaper one. The Windows worker does not auto-run `agent:dsh` + `type:analyze`: headless read-only denies writes but does not confine local reads or network.

Inside one DSH turn, call `subagent_codex` once when a coding step is required. That call is the handoff. Do not start a second implementation in parallel unless Partner says so.

## Codex / 实现

Code changes, debugging, tests, PR-quality engineering. Prefer Codex for implementation (`gpt-6.1-sol`).

Two ways in, for a person at the machine:

- DSH calls `subagent_codex` inside one DSH turn
- native CLI at `%LOCALAPPDATA%\OpenAI\Codex\bin\14b838c1d074ff01\codex.exe` (0.167.0-alpha.3.2, not on PATH)

The public worker does not start `codex.exe`. `codex exec -s read-only` can still read the home directory and can still load MCP or user config, and it is not a write sandbox. `dangerously-bypass` is refused. When `lanes.enabled.implement` is true, implementation goes through DSH headless to one `subagent_codex` call inside a fresh clone. That is not a full isolation boundary either: DSH workspace-write fences writes to that clone, and reads and network stay open.

## Hermes / 分析（CLI）

Hermes 0.22.7 can run a DeepSeek oneshot on Windows. [Issue #4](https://github.com/Siniv26/agent-hub/issues/4) closed PASS with `HERMES_ANALYSIS_OK` for the binary itself.

When `lanes.enabled.analyze` is true, the worker sends only the validated Goal line to Hermes:

```text
hermes -z "<brief>" --provider deepseek -m deepseek-flash --safe-mode --ignore-rules -t todo
```

Empty `-t` means every tool and is never used. `memory`, `file`, `terminal`, `browser`, and `delegation` are refused. `--safe-mode` turns off user config, rules, memory injection, plugins, shell hooks, and MCP. The process keeps the existing `HERMES_HOME` so Hermes can read its own provider key. Credentials are not copied into a fresh home. This is not a full filesystem or network sandbox. If the binary is missing, or `--help` does not show `--safe-mode`, `--ignore-rules`, and `-t`, the worker does not start the model.

Binary: `%LOCALAPPDATA%\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\hermes\bin\hermes.exe`

`HERMES_HOME` is that `Local\hermes` directory (`...\LocalCache\Local\hermes`), not `~\.hermes`.

## Who runs / 派工

| Signal | Route | Labels |
| --- | --- | --- |
| Analysis text only | Hermes oneshot, worker lane when enabled | `agent:hermes` `type:analyze` |
| Harness UI, or analysis that must call Codex | DSH, by a person. The worker does not auto-run this pair | `agent:dsh` `type:analyze` |
| Code, tests, debugging, PR-quality change | DSH → one `subagent_codex`, worker lane when enabled | `agent:codex` or `agent:dsh`, plus `type:implement` |
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

v1.1 is default off and lives under `%LOCALAPPDATA%\AgentHub\worker`. It claims one opted-in Issue from author `Siniv26` with `bus:ready` and `status:todo`. Comments are ignored. The claim snapshot is immutable. A child that already started is not started again. A failed GitHub write is not retried. A timeout kills the child.

Smoke (`type:smoke`, `agent:partner`, risk `none`) stays the default automatic task and closes only after `BUS_SMOKE_OK`.

Two lanes are off until config says otherwise. Both still require `enabled: true`, `dry_run: false`, and `arm`:

- `lanes.enabled.analyze` — `agent:hermes` + `type:analyze`. Hermes oneshot as above. The comment is sanitized and truncated. The Issue is not closed. Partner reads it.
- `lanes.enabled.implement` — `agent:codex` or `agent:dsh`, plus `type:implement`. DSH headless in a fresh clone of this repo, `--permission-mode workspace-write`, `--approval-policy never`, `--json`, one `subagent_codex`. Merge, deploy, and delete are refused in the prompt and killed if a shell tool tries them. The Issue closes only when the run completed, Codex was called once, and the output has one `https://github.com/Siniv26/agent-hub/pull/<n>` URL. Anything ambiguous stays `status:blocked`.

`agent:dsh` + `type:analyze` stays deferred: headless read-only does not confine local reads or network on Windows. Native `codex.exe` stays deferred.

Risk `approval` or `high` is blocked for a human. The worker body is only `## Goal`, `## Task`, and `## Risk`. The longer Task template is not auto-run.

Runtime GitHub auth is `gh auth login` on that machine (gh 2.103.3 user scope; device code). The Cursor GitHub connector is not runtime auth. The worker drops `GH_TOKEN` and does not read credential files into the prompt or the comment.

Operations: [OPS.zh.md](OPS.zh.md). Templates: [bus-task](../.github/ISSUE_TEMPLATE/bus-task.md), [bus-analyze](../.github/ISSUE_TEMPLATE/bus-analyze.md), [bus-implement](../.github/ISSUE_TEMPLATE/bus-implement.md).
