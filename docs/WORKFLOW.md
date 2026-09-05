# WORKFLOW — the AI-native rules this repo runs on

Basis: Clare Liguori (AWS), "From AI-Assisted to AI-Native: Building a Frontier Development Team"
(https://youtu.be/Ry0WHNxDbYA · AWS blog: https://aws.amazon.com/blogs/machine-learning/how-frontier-teams-are-reinventing-ai-native-development/)
The operating principles are the same as the WORKFLOW of sheet_mcp and retail-mcp. Only the shared summary plus **what is specific to this repo** is written here.

## 0. Roles (the three frontier behaviors)

| Behavior | In this repo |
|---|---|
| Hands-off Coding (1–2%) | The maintainer only edits and reviews SPEC/DESIGN, runs the smoke, and approves public releases. Implementation is done by the agent |
| Infrequent Interaction | Every task has machine-checkable completion criteria → the agent runs to completion with no mid-session intervention |
| Minimized Idle Time | Lanes A/B/C after T1, T6/T8/T9 after T5, in parallel. Cross-repo parallelism with sheet_mcp and retail-mcp is possible too |

## 1. Five habits → rules (shared summary)

1. **Agent Context** — tribal knowledge lives only in CLAUDE.md and docs. Biweekly pruning with a log.
2. **Slow Down to Speed Up** — this repo's translation: **it is Python, but types are not given up.** pyright strict + Pydantic take the role of tsc and give the agent its cheapest feedback loop. Sprinkling `# type: ignore` violates this habit.
3. **Feed, Don't Babysit** — assignment is one TASKS template, self-verification is `make check`.
   ```bash
   git worktree add ../lang_ai_agent-t3 -b t3 && cd ../lang_ai_agent-t3 && claude
   ```
4. **Explicit Intent** — a change to the graph topology, the state schema, or the SSE contract lands as a DESIGN diff before code.
5. **Shift Left** — this repo's translation: **replace the model with a script (ScriptedChatModel)**. In an LLM app the "local deterministic mock service" is a fake model. Real models exist only in smoke and evals.

## 2. Specific to lang_ai_agent

- **No-flake principle**: if a test wobbles, the cause is the script or the code. A fix that puts a real-model call into a test to make it pass "plausibly" is rejected in review.
- **The approval gate is non-negotiable**: adding a shortcut that lets an effect tool bypass approval (convenience flags, test-only backdoors included) is forbidden. Deleting or weakening the structural-invariant test is forbidden too.
- **State diet**: state is serialized whole at every checkpoint. Review catches any diff that puts a large payload into state.
- **Public-repo awareness**: commit messages, comments and error messages must be fit to be public. No secrets, no traces of clients.
- **English everywhere** (since 2026-09-05): docs, code comments, commit messages, and user-facing strings are in English. The repo targets the global contracting market and the package is public.

## 3. Daily routine

1. Check which tasks are ready → assign a worktree per lane (the backlogs of the three repos are treated as one queue)
2. Do not intervene while a task runs — spend that time polishing v0.2 docs and the demo scenario
3. Completion report → re-run `make check` → review the diff → merge → update status
4. Biweekly: prune CLAUDE.md, tidy TASKS

## 4. Limits of autonomy (what the maintainer holds)

- Running the real-model smoke (API cost) and deciding the default model tier
- Switching `SEND_MODE=live` and approving real side effects
- Approving the GitHub publication and the final tone of the English README
- Local environment setup such as secrets and MCP server paths
- **Approving a production PyPI release** — the same version cannot be re-uploaded after deletion, so this is irreversible on the level of `SEND_MODE=live`. Up to the TestPyPI release the agent may proceed autonomously.

These approvals concern the maintainer of this repo. None of them applies to people who install the package: installing and running `lang-ai-agent` never requires anyone's approval. The only "approval" an end user meets is the one their own agent asks for before running a side-effecting tool, and that user is the approver.
