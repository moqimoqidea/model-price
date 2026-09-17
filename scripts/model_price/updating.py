"""Fast-forward this skill from its Git upstream before an explicit refresh."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .core import now_iso
from .errors import SkillUpdateError
from .paths import SKILL_DIR
from .text import clean_text

SELF_UPDATE_ENV = "MODEL_PRICE_SELF_UPDATE_RESULT"


class GitSkillUpdater:
    """Fast-forward this skill from its configured Git upstream."""

    def __init__(self, skill_dir: Path = SKILL_DIR, runner: Any = None) -> None:
        self.skill_dir = skill_dir.resolve()
        self.runner = runner or subprocess.run

    def _git(self, *arguments: str, allowed_codes: tuple[int, ...] = (0,)) -> Any:
        try:
            result = self.runner(
                ["git", *arguments],
                cwd=self.skill_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SkillUpdateError(str(exc)) from exc
        if result.returncode not in allowed_codes:
            detail = clean_text(result.stderr or result.stdout) or "git command failed"
            raise SkillUpdateError(detail)
        return result

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
            self._git("fetch", "--quiet")
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
