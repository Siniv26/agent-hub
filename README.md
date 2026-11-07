# agent-hub

GitHub Issues are the task bus. Four agents share this repo; Partner coordinates, the others take labeled Issues.

| Agent | Role |
| --- | --- |
| **Partner** (Grok Bot) | Triage, create and update Issues, decide who runs, phone and remote coordination. |
| **DSH** (DeepSeek Harness, deepseek-flash) | Analysis, summarization, planning, cheap classification, draft Issue bodies. |
| **Codex** (gpt-6.1-sol via native CLI bridge) | Code changes, debugging, tests, PR-quality engineering. |
| **Hermes** | Local Windows execution / ACP. Later; not on the live path yet. |

- Routing: [docs/ROUTING.md](docs/ROUTING.md)
- DSH → Codex smoke: [docs/SMOKE.md](docs/SMOKE.md)
- New task: [Task issue template](.github/ISSUE_TEMPLATE/task.md)

No secrets in Issues, comments, or docs.
