"""Run a Claude Code slash command headlessly (`claude -p`) on the owner's subscription. Never the API.

- Refuses to run if ANTHROPIC_API_KEY (or another Anthropic API credential) is set: Claude Code would bill the API.
- Each command runs with --permission-mode dontAsk and an explicit allow-list, so it can only use the project's own
  CLIs, write to data/handoff/, read files, and (for outreach) search the web.
- A usage limit stops the step cleanly; the caller records it and retries after the reset. No extra usage is bought.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .rules import parse_limit

API_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
BASE_TOOLS = ["Read", "Glob", "Grep", "Write(data/handoff/**)", "Edit(data/handoff/**)",
              "Bash(.venv/Scripts/python -m applications:*)", "Bash(.venv/Scripts/python -m outreach:*)",
              "Bash(.venv\\Scripts\\python -m applications:*)", "Bash(.venv\\Scripts\\python -m outreach:*)",
              "Bash(.venv/bin/python -m applications:*)", "Bash(.venv/bin/python -m outreach:*)"]
WEB_TOOLS = ["WebSearch", "WebFetch"]
NEEDS_WEB = {"draft-outreach", "redraft-outreach", "prep-call"}
TIMEOUT_SECONDS = 30 * 60


@dataclass
class ClaudeResult:
    status: str  # ok | limit | error | refused | missing
    output: str = ""
    reset: str = ""
    seconds: float = 0.0


def api_key_present(env: Optional[dict] = None) -> Optional[str]:
    env = os.environ if env is None else env
    return next((k for k in API_ENV_VARS if env.get(k)), None)


def claude_path() -> Optional[str]:
    found = shutil.which("claude")
    if found:
        return found
    # scheduled runs (Task Scheduler, launchd) often start with a short PATH: look in the usual install places too
    places = [Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude"),
              Path.home() / ".claude" / "local" / "claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")]
    return next((str(x) for x in places if x.exists()), None)


def run_command(command: str, args: str, cwd: Path, runner: Optional[Callable] = None,
                env: Optional[dict] = None, timeout: int = TIMEOUT_SECONDS) -> ClaudeResult:
    """`command` without the slash, e.g. "score-jobs"; `args` e.g. "15"."""
    import time
    env = dict(os.environ if env is None else env)
    key = api_key_present(env)
    if key:
        return ClaudeResult("refused", f"{key} is set in the environment. Claude Code would use (and bill) the API, so the "
                                       f"autopilot won't run AI steps. Remove it to continue.")
    exe = claude_path() if runner is None else "claude"
    if not exe:
        return ClaudeResult("missing", "The `claude` command wasn't found. Is Claude Code installed?")
    tools = BASE_TOOLS + (WEB_TOOLS if command in NEEDS_WEB else [])
    cmd = [exe, "-p", f"/{command} {args}".strip(), "--permission-mode", "dontAsk", "--allowedTools", *tools]
    start = time.monotonic()
    try:
        proc = (runner or subprocess.run)(cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
                                          encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return ClaudeResult("error", f"/{command} took longer than {timeout // 60} minutes and was stopped.",
                            seconds=time.monotonic() - start)
    except OSError as e:
        return ClaudeResult("error", f"couldn't start claude: {e}")
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    limited, reset = parse_limit(out)
    if limited:
        return ClaudeResult("limit", out[-2000:], reset, time.monotonic() - start)
    if proc.returncode != 0:
        return ClaudeResult("error", out[-2000:], seconds=time.monotonic() - start)
    return ClaudeResult("ok", out[-4000:], seconds=time.monotonic() - start)
