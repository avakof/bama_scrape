# Run the Bama Scrape agent team

The workflow runs on demand in Orca. The coordinator gives the researcher a set of public questions, then uses the findings to brief three coding workers in parallel. It reviews their reports and commits, integrates the selected changes, and runs the project checks before reporting back.

| Role | Runner and model | Assignment |
|---|---|---|
| Coordinator | Codex · GPT‑6.2 Astra · Ultra | Scope the work, dispatch the team, and verify the combined change |
| Researcher | Codex · GPT‑6.2 Sol · High | Search the live web and return sources, uncertainties, and implementation implications |
| Implementation | Claude Code · Claude Opus 5.5 · High | Implement the main change |
| Complementary implementation | Codex · GPT‑6.2 Sol · High | Own an independent implementation slice or review and fix a targeted area |
| Regression coverage | OpenCode 2 · DeepSeek V4.1 Flash Free · Max | Implement regression coverage or a separate coding slice |

## Start a run

Open this checkout in a running Orca app with orchestration enabled. Codex and Claude Code need signed-in accounts. The OpenCode worker uses the free DeepSeek model in the OpenCode catalog. Run from the project root with Python 3.11 or newer.

```sh
python3 tools/agent_team.py doctor
python3 tools/agent_team.py start "Describe the Bama Scrape change you want"
```

Omit the objective to open the coordinator and type the task in Orca. Check the coordinator conversation for research updates, questions, and the final result. Orca's orchestration view shows the workers and their outputs. The coding branches start at the coordinator's committed `HEAD`. If the task depends on local edits, tell the coordinator which changes are part of the brief. Worker branches and their results are retained locally for review.

Codex uses the repository's live web search setting when this checkout is trusted. The OpenCode project config applies `max` as the selected DeepSeek model's request default. The launcher checks that OpenCode still offers the model and reasoning level before an objective starts.

## Skills

The [coordinator skill](../../.agents/skills/bama-agent-workflow/SKILL.md) defines the dispatch and handoff. Workers receive their role guidance in the task brief, including on a fresh branch. The [Bama coding skill](../../.agents/skills/bama-code/SKILL.md) covers the repository's offline tests and fixture rules. The [Bama research skill](../../.agents/skills/bama-research/SKILL.md) sets the evidence standard for public-web research.

The repository includes these pinned Markdown resources with their MIT licenses.

- Coordinator and coding workers use the [Superpowers verification skill distributed by OpenAI](https://github.com/openai/plugins/tree/981b02c0c44058b595f3b4180002c3ae3c806d40/plugins/superpowers/skills/verification-before-completion).
- The Codex coding worker also uses the [GitHub review and refactor skill](https://github.com/github/awesome-copilot/tree/f10fdb8b2660ce85c98f220b2f1ca24653768a5c/skills/review-and-refactor) within its assigned scope.
- The researcher uses Eric Andrade's [evidence ledger](https://github.com/ericgandrade/claude-superskills/blob/394587c7cb1fb249d02326c96d9735a0d5969b23/skills/deep-research/references/evidence-ledger.md) with Codex web search.
