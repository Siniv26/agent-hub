# Smoke: DSH → Codex / 冒烟

One shared task. DSH analyzes a tiny ask, calls `subagent_codex` once, and Partner closes the Issue.

## Setup / 准备

1. Partner opens an Issue with the Task template.
2. Title: `smoke: DSH calls Codex once`.
3. Labels: `agent:dsh`, `type:smoke`, `status:todo`.
4. **Goal:** a tiny engineering reply, either the line `PONG` or a one-line note.
5. **Owner agent:** DSH. **Risk:** `none`. **Inputs:** this checklist. No secrets.
6. **Done-when:** the three checks in Pass are true.

## Run / 执行

1. Partner sets `status:doing` and hands the Issue to DSH.
2. DSH comments a short analysis only: what the reply should be.
3. DSH calls `subagent_codex` **once** for that reply.
4. Codex returns only the tiny artifact (`PONG`, or one line). DSH pastes it on the Issue.
5. Partner comments the result, sets `agent:partner` and `status:done`.

## Pass / 通过

- [ ] The Issue has Goal, Owner agent, Inputs, Done-when, Risk, and Notes.
- [ ] DSH's analysis comment is on the Issue before the Codex call.
- [ ] Exactly one `subagent_codex` call, and the reply is only `PONG` or one line.
- [ ] Partner commented and set `status:done`.

## Stop / 停止

Set `status:blocked` and stop if Codex is called more than once, the reply becomes a real code change, or any step needs delete, an external send, spending, or another irreversible action.
