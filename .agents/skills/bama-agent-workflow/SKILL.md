---
name: bama-agent-workflow
description: Coordinate the Bama Scrape researcher and three coding workers in Orca, then integrate and verify their results.
---

# Bama Scrape agent workflow

You are the coordinator. Run `orca skills get orchestration` for the installed runtime's lifecycle and recovery instructions. Read the project guardrails in `docs/contributing.md`. Use `.agents/skills/verification-before-completion/SKILL.md` when verifying the integrated result.

1. For each user objective, run `python3 tools/agent_team.py doctor` and create a new Orca Run with `orca orchestration run-create --objective "<objective>" --json`. Retain its ID. Keep the user brief, task state, and researcher findings in the coordinator conversation. Use a temporary file outside the repository to pass briefs to the launcher.
2. Write a research brief with the user's objective, the public questions to investigate, and the evidence that would help implementation. Dispatch it with `python3 tools/agent_team.py research --run <run-id> --brief <brief.md>`. Follow any `next` action in its receipt. End your turn while Orca is waiting for the worker and resume when Orca delivers its report. Use `orca orchestration inbox --terminal run:<run-id> --full` to recover already delivered messages if needed.
3. Use the research findings to write three independent coding briefs with file scope, acceptance checks, and any relevant citations. Give Claude the main implementation, Codex a complementary implementation or an independent review with targeted fixes, and OpenCode regression coverage or another concrete coding slice. For a change too small to divide, assign independent patch proposals or verification work and choose the best changes. Write a JSON object with one string per role under `claude`, `codex`, and `opencode`. Dispatch them with `python3 tools/agent_team.py code --run <run-id> --plan <briefs.json>`. The launcher gives each worker its own Orca child worktree from the same committed base.
4. Inspect every start receipt and retain each role's task and dispatch IDs in the coordinator conversation. Match incoming reports to those IDs and the run they belong to. Start the coding batch once per run. Follow `next` when present. On a failed or uncertain launch, use the orchestration guide's recovery procedure and inspect the original dispatch before retrying. Keep the run open while worker reports are pending and let Orca wake you. Return questions using `orca orchestration reply` when the brief already answers them.
5. Review the reported diffs and checks. Integrate selected commits into the coordinator worktree only after checking file ownership, overlap, and any user edits. Run the relevant offline checks on the combined change. Return one result to the user with the research citations, selected work, combined verification, and unresolved blockers. A report of failure or a launch error does not settle the task as complete.

Use Orca for all four assignments and wait for their results. Preserve user changes and keep any code integration local unless the user requests a push or deployment.
