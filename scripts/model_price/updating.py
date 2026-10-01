"""Fast-forward this skill from its Git upstream before an explicit refresh."""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.request import getproxies

from .core import (
    BROWSER_HEADERS,
    DEFAULT_USER_AGENT,
    RETRYABLE_HTTP_STATUSES,
    exponential_backoff,
    now_iso,
)
from .errors import SkillUpdateError
from .paths import SKILL_DIR
from .text import clean_text

SELF_UPDATE_ENV = "MODEL_PRICE_SELF_UPDATE_RESULT"
GIT_TRANSIENT_MARKERS = (
    "could not resolve host",
    "failed to connect",
    "connection reset",
    "connection timed out",
    "connection refused",
    "network is unreachable",
    "network unavailable",
    "operation timed out",
    "remote end hung up",
    "recv failure",
    "send failure",
    "ssl connect error",
)


def git_network_environment() -> dict[str, str]:
    """Share the browser identity and OS proxies without changing Git settings."""
    environment = os.environ.copy()
    environment["GIT_HTTP_USER_AGENT"] = DEFAULT_USER_AGENT
    count = int(environment.get("GIT_CONFIG_COUNT", "0"))
    # Git owns its protocol's Accept and content headers; the browser identity
    # fields are the same ones the public-document client uses.
    for name, value in BROWSER_HEADERS.items():
        if name in {"accept", "user-agent"}:
            continue
        environment[f"GIT_CONFIG_KEY_{count}"] = "http.extraHeader"
        environment[f"GIT_CONFIG_VALUE_{count}"] = f"{name}: {value}"
        count += 1
    environment["GIT_CONFIG_COUNT"] = str(count)
    # urllib sees macOS system proxies that Git's libcurl does not inherit.
    # Explicit environment and repository proxy settings still take precedence.
    for scheme, value in getproxies().items():
        if scheme not in {"http", "https", "all", "no"}:
            continue
        variable = f"{scheme}_proxy"
        if variable not in environment and variable.upper() not in environment:
            environment[variable] = value
    return environment


class GitSkillUpdater:
    """Fast-forward this skill from its configured Git upstream."""

    def __init__(
        self,
        skill_dir: Path = SKILL_DIR,
        runner: Any = None,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.skill_dir = skill_dir.resolve()
        self.runner = runner or subprocess.run
        self._sleep = sleeper
        self._uniform = uniform

    def _git(self, *arguments: str, allowed_codes: tuple[int, ...] = (0,)) -> Any:
        try:
            result = self.runner(
                ["git", *arguments],
                cwd=self.skill_dir,
                capture_output=True,
                text=True,
                timeout=60,
                env=git_network_environment(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SkillUpdateError(str(exc)) from exc
        if result.returncode not in allowed_codes:
            detail = clean_text(result.stderr or result.stdout) or "git command failed"
            raise SkillUpdateError(detail)
        return result

    def _fetch(self) -> None:
        """Only the idempotent network read is retried, never a Git mutation."""
        for attempt in range(3):
            try:
                self._git("fetch", "--quiet")
                return
            except SkillUpdateError as exc:
                detail = str(exc).lower()
                status = re.search(r"(?:http\s*|returned error:\s*)(\d{3})", detail)
                transient = (
                    isinstance(exc.__cause__, subprocess.TimeoutExpired)
                    or any(marker in detail for marker in GIT_TRANSIENT_MARKERS)
                    or bool(status and int(status[1]) in RETRYABLE_HTTP_STATUSES)
                )
                if not transient or attempt == 2:
                    raise
                self._sleep(exponential_backoff(attempt, uniform=self._uniform))

    def update(self) -> dict[str, Any]:
        checked_at = now_iso()
        result: dict[str, Any] = {"status": "check_failed", "checked_at": checked_at}
        try:
            repository = Path(
                self._git("rev-parse", "--show-toplevel").stdout.strip()
            ).resolve()
            skill_path = self.skill_dir.relative_to(repository).as_posix()
            upstream = self._git(
                "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
            ).stdout.strip()
            result.update({"repository": str(repository), "upstream": upstream})

            # Fetch before comparing trees so every explicit price refresh first
            # checks the configured remote for a newer copy of this skill.
            self._fetch()
            before = self._git("rev-parse", "HEAD").stdout.strip()
            after = self._git("rev-parse", "@{upstream}").stdout.strip()
            result.update({"from_revision": before, "to_revision": after})

            skill_changed = self._git(
                "diff",
                "--quiet",
                f"{before}..{after}",
                "--",
                skill_path,
                allowed_codes=(0, 1),
            ).returncode == 1
            if not skill_changed:
                result["status"] = "up_to_date"
                return result

            can_fast_forward = self._git(
                "merge-base", "--is-ancestor", before, after, allowed_codes=(0, 1)
            ).returncode == 0
            if not can_fast_forward:
                result.update(
                    status="update_skipped",
                    reason="local branch and upstream have diverged",
                )
                return result

            if self._git("status", "--porcelain").stdout.strip():
                result.update(
                    status="update_skipped",
                    reason="working tree has local changes",
                )
                return result

            self._git("merge", "--ff-only", upstream)
            result["status"] = "updated"
            return result
        except (SkillUpdateError, ValueError) as exc:
            result["reason"] = str(exc)
            return result


def update_skill_before_refresh(refresh: bool) -> dict[str, Any] | None:
    """Update before source I/O and restart once when the skill changed."""
    if not refresh:
        return None
    carried = os.environ.get(SELF_UPDATE_ENV)
    if carried:
        try:
            return json.loads(carried)
        except json.JSONDecodeError:
            pass

    result = GitSkillUpdater().update()
    if result["status"] == "updated":
        environment = os.environ.copy()
        environment[SELF_UPDATE_ENV] = json.dumps(result, ensure_ascii=False)
        os.execve(sys.executable, [sys.executable, *sys.argv], environment)
    return result
