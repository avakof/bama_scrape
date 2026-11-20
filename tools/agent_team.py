#!/usr/bin/env python3
"""Launch the Bama Scrape agent team through the local Orca runtime."""

import argparse
import json
import os
import platform
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".agents" / "skills"
LEAD = ("codex", "gpt-6.2-astra", "ultra")
GPT_WORKER = ("codex", "gpt-6.2-sol", "high")
OPENCODE_MODEL = "opencode/deepseek-v4.1-flash-free"
OPENCODE_CONFIG = Path(".opencode/opencode.json")
CODERS = {
    "claude": ("claude", "claude-opus-5-5", "high"),
    "codex": GPT_WORKER,
    "opencode": ("opencode", OPENCODE_MODEL, None),
}


def orca_executable():
    if command := os.environ.get("ORCA_CLI_COMMAND"):
        return command
    if os.environ.get("ORCA_DEV_REPO_ROOT"):
        return "orca-dev"
    return (
        "orca-ide"
        if platform.system() == "Linux" and not os.environ.get("ORCA_WORKTREE_ID")
        else "orca"
    )


def command(*args, input_text=None):
    return subprocess.run(
        args, cwd=ROOT, input=input_text, capture_output=True, text=True, check=False
    )


def orca(*args, input_text=None):
    return command(orca_executable(), *args, "--json", input_text=input_text)


def result(process):
    if process.returncode:
        raise ValueError(process.stdout.strip() or process.stderr.strip())
    return json.loads(process.stdout)


def model_args(profile):
    agent, model, effort = profile
    args = ["--agent", agent, "--model", model]
    if effort:
        args.extend(["--effort", effort])
    return args


def doctor():
    workspace = result(orca("worktree", "current"))["result"]["worktree"]
    installed = result(orca("agent", "installed"))["result"]["agents"]
    missing = {"codex", "claude", "opencode"} - {entry["id"] for entry in installed}
    if missing:
        raise ValueError(
            f"Orca cannot launch these agents on this host: {', '.join(sorted(missing))}"
        )

    if command("codex", "login", "status").returncode:
        raise ValueError("Codex requires sign-in. Run `codex login`.")
    if command("claude", "auth", "status").returncode:
        raise ValueError("Claude Code requires sign-in. Run `claude auth login`.")

    catalog = result(command("opencode", "api", "--standalone", "model.list"))["data"]
    for entry in catalog:
        if f"{entry['providerID']}/{entry['id']}" == OPENCODE_MODEL:
            variants = {option["id"] for option in entry.get("variants", [])}
            if variants == {"max"} and entry["settings"].get("reasoningEffort") == "max":
                print(f"Ready in {workspace['path']}. Codex and Claude are signed in.")
                print(f"OpenCode exposes {OPENCODE_MODEL} with max reasoning configured.")
                return
    raise ValueError(f"OpenCode did not load {OPENCODE_MODEL} with max reasoning in this project.")


def skill(name, path="SKILL.md"):
    text = (SKILLS / name / path).read_text(encoding="utf-8")
    return text.split("---\n", 2)[-1].strip() if text.startswith("---\n") else text.strip()


def publish(process):
    if process.stdout:
        print(process.stdout.rstrip())
    if process.stderr:
        print(process.stderr.rstrip(), file=sys.stderr)
    return process.returncode


def start(objective):
    prompt = (
        "You are the Bama Scrape workflow coordinator. Read and follow "
        "`.agents/skills/bama-agent-workflow/SKILL.md` for this session. "
        "Use Orca workers for all four roles."
    )
    if objective:
        doctor()
        prompt += f"\n\nUser objective\n{objective}"
    else:
        prompt += "\n\nIntroduce yourself briefly and wait for the user's project task."
    return publish(orca("agent", "launch", *model_args(LEAD), "--prompt", prompt))


def worker(run_id, title, profile, brief, guidance, *placement):
    spec = f"# Assignment\n\n{brief}\n\n# Role skills\n\n{guidance}\n"
    return orca(
        "orchestration",
        "worker-start",
        "--run",
        run_id,
        "--task-title",
        title,
        "--spec-file",
        "-",
        *model_args(profile),
        *placement,
        input_text=spec,
    )


def read_brief(path):
    brief = path.read_text(encoding="utf-8").strip()
    if not brief:
        raise ValueError(f"The brief is empty: {path}")
    return brief


def research(run_id, brief):
    guidance = "\n\n".join(
        [
            skill("bama-research"),
            skill("bama-research", "references/evidence-ledger.md"),
        ]
    )
    return publish(
        worker(
            run_id, "Researcher", GPT_WORKER, read_brief(brief), guidance, "--worktree", "current"
        )
    )


def code(run_id, plan_file):
    plan = json.loads(read_brief(plan_file))
    if (
        not isinstance(plan, dict)
        or plan.keys() != CODERS.keys()
        or any(not isinstance(brief, str) or not brief.strip() for brief in plan.values())
    ):
        raise ValueError(
            "The plan must give a non-empty brief each for claude, codex, and opencode."
        )

    result(orca("orchestration", "run-show", "--id", run_id))
    head = command("git", "rev-parse", "HEAD")
    if head.returncode:
        raise ValueError(head.stderr.strip())
    base = head.stdout.strip()
    batch = uuid.uuid4().hex[:8]
    guidance = f"{skill('bama-code')}\n\n{skill('verification-before-completion')}"
    review = (
        f"{guidance}\n\nApply the following guidance within your assigned scope "
        "using this project's `docs/contributing.md` as the coding guidelines.\n\n"
        f"{skill('review-and-refactor')}"
    )

    # Orca's model selector for OpenCode requires an existing worktree.
    open_worktree = result(
        orca(
            "worktree",
            "create",
            "--name",
            f"bama-opencode-{batch}",
            "--base-branch",
            base,
            "--setup",
            "skip",
        )
    )["result"]["worktree"]
    # Give the new worker the settings checked in the coordinator's checkout.
    workspace_config = Path(open_worktree["path"]) / OPENCODE_CONFIG
    config = (ROOT / OPENCODE_CONFIG).read_bytes()
    if not workspace_config.exists() or workspace_config.read_bytes() != config:
        workspace_config.parent.mkdir(parents=True, exist_ok=True)
        workspace_config.write_bytes(config)

    with ThreadPoolExecutor(max_workers=3) as pool:
        starts = {}
        for role, profile in CODERS.items():
            if role == "opencode":
                placement = ("--worktree", f"id:{open_worktree['id']}")
            else:
                placement = (
                    "--worktree",
                    "new-child",
                    "--name",
                    f"bama-{role}-{batch}",
                    "--base-branch",
                    base,
                    "--setup",
                    "skip",
                )
            starts[role] = pool.submit(
                worker,
                run_id,
                f"{role.capitalize()} coding",
                profile,
                plan[role],
                review if role == "codex" else guidance,
                *placement,
            )

    failed = False
    for role, future in starts.items():
        print(f"[{role}]")
        try:
            failed |= publish(future.result()) != 0
        except OSError as exc:
            print(exc, file=sys.stderr)
            failed = True
    return int(failed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("doctor", help="Check Orca, account sign-in, and OpenCode's model")
    start_parser = commands.add_parser(
        "start", help="Open the GPT-6.2 Astra Ultra coordinator in Orca"
    )
    start_parser.add_argument("objective", nargs="?", help="Optional project task for this run")
    for name, flag in [("research", "--brief"), ("code", "--plan")]:
        subparser = commands.add_parser(name, help=f"Dispatch {name} work from the coordinator")
        subparser.add_argument("--run", required=True, help="Orca orchestration run ID")
        subparser.add_argument(flag, required=True, type=Path)
    args = parser.parse_args()

    try:
        if args.action == "doctor":
            doctor()
            return 0
        if args.action == "start":
            return start(args.objective)
        if args.action == "research":
            return research(args.run, args.brief)
        return code(args.run, args.plan)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Agent team: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
