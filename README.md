# agent-hub

GitHub Issues are the task bus. Four agents share this repo; Partner coordinates, the others take labeled Issues.

| Agent | Role |
| --- | --- |
| **Partner** (Grok Bot) | Triage, create and update Issues, decide who runs. |
| **DSH** (DeepSeek Harness) | Harness UI and `subagent_codex` orchestration. |
| **Codex** | Implementation via DSH `subagent_codex` or the native CLI (current hashed OpenAI Codex bin). |
| **Hermes 0.22.7** | Windows oneshot DeepSeek analysis (`deepseek-flash`). Verified on Issue #4. |

- Routing: [docs/ROUTING.md](docs/ROUTING.md)
- DSH → Codex smoke: [docs/SMOKE.md](docs/SMOKE.md)
- New task: [Task issue template](.github/ISSUE_TEMPLATE/task.md)

Phone/remote entry (Telegram gateway, Tailscale tunnel to serve/dashboard) is deferred. No secrets in Issues, comments, or docs.
