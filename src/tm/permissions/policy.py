"""Permission policy: allow/deny/ask rules for files, shell, and network.

Rules are loaded from ``<config>/policy.toml`` (global) and
``<cwd>/.aiagent/policy.toml`` (project). Deny rules always win, then allow
rules, then the ``default`` decision.
"""

from __future__ import annotations

import contextlib
import fnmatch
import tomllib
from pathlib import Path

from pydantic import BaseModel

from tm.permissions.actions import Action, ActionKind, Decision


class RuleSet(BaseModel):
    allow: list[str] = []
    deny: list[str] = []


def _posix(value: str) -> str:
    return value.replace("\\", "/")


def _path_candidates(target: str, cwd: Path) -> list[str]:
    candidates = {_posix(target)}
    path = Path(target).expanduser()
    if not path.is_absolute():
        path = cwd / path
    candidates.add(_posix(str(path)))
    candidates.add(_posix(path.name))
    with contextlib.suppress(ValueError):
        candidates.add(_posix(str(path.resolve().relative_to(cwd.resolve()))))
    return [c for c in candidates if c]


def _match_any(patterns: list[str], candidates: list[str]) -> bool:
    for pattern in patterns:
        for candidate in candidates:
            if fnmatch.fnmatch(candidate, pattern):
                return True
    return False


def _pattern_forms(pattern: str) -> list[str]:
    """Expand a rule into the forms that make it folder-aware.

    A rule naming a directory (``src`` or ``src/**``) should cover the whole
    subtree, so ``src`` also matches ``src/a/b.txt``. ``**`` already matches
    across separators.
    """
    base = _posix(pattern).rstrip("/")
    if not base:
        base = _posix(pattern)
    forms = {base, f"{base}/**"}
    if base.endswith("/**"):
        forms.add(base[:-3].rstrip("/"))
    return [form for form in forms if form]


def _file_matches(patterns: list[str], candidates: list[str]) -> bool:
    for pattern in patterns:
        for form in _pattern_forms(pattern):
            for candidate in candidates:
                if fnmatch.fnmatch(candidate, form):
                    return True
    return False


def _command_candidates(command: str) -> list[str]:
    tokens = command.strip().split()
    candidates = {command.strip()}
    if tokens:
        candidates.add(tokens[0])
    return [c for c in candidates if c]


class Policy(BaseModel):
    default: Decision = Decision.ASK
    #: Reads are safe, so they are allowed unless a read rule says otherwise.
    default_read: Decision = Decision.ALLOW
    files_read: RuleSet = RuleSet()
    files_write: RuleSet = RuleSet()
    shell: RuleSet = RuleSet()
    network: RuleSet = RuleSet()
    cwd: Path = Path(".")

    @classmethod
    def from_dict(cls, data: dict, cwd: Path | None = None) -> Policy:
        files = data.get("files", {})
        return cls(
            default=Decision(str(data.get("default", "ask")).lower()),
            default_read=Decision(str(data.get("default_read", "allow")).lower()),
            files_read=RuleSet.model_validate(files.get("read", {})),
            files_write=RuleSet.model_validate(files.get("write", {})),
            shell=RuleSet.model_validate(data.get("shell", {})),
            network=RuleSet.model_validate(data.get("network", {})),
            cwd=(cwd or Path.cwd()).resolve(),
        )

    @classmethod
    def load(
        cls,
        cwd: Path | None = None,
        config_dir: Path | None = None,
        *,
        include_project: bool = True,
    ) -> Policy:
        working_dir = (cwd or Path.cwd()).resolve()
        paths: list[Path] = []
        if config_dir is not None:
            paths.append(config_dir / "policy.toml")
        if include_project:
            paths.append(working_dir / ".aiagent" / "policy.toml")

        merged: dict = {}
        for path in paths:
            if not path.exists():
                continue
            with path.open("rb") as handle:
                data = tomllib.load(handle)
            merged = _merge(merged, data)
        return cls.from_dict(merged, working_dir)

    def _rules_for(self, kind: ActionKind) -> RuleSet:
        return {
            ActionKind.FILE_READ: self.files_read,
            ActionKind.FILE_WRITE: self.files_write,
            ActionKind.SHELL: self.shell,
            ActionKind.NETWORK: self.network,
        }[kind]

    def _matches(self, patterns: list[str], action: Action) -> bool:
        if not patterns:
            return False
        if action.kind in (ActionKind.FILE_READ, ActionKind.FILE_WRITE):
            return _file_matches(patterns, _path_candidates(action.target, self.cwd))
        if action.kind is ActionKind.SHELL:
            return _match_any(patterns, _command_candidates(action.target))
        return _match_any([p.lower() for p in patterns], [_posix(action.target).lower()])

    def evaluate(self, action: Action) -> tuple[Decision, str]:
        rules = self._rules_for(action.kind)
        if self._matches(rules.deny, action):
            return Decision.DENY, "matched a deny rule"
        if self._matches(rules.allow, action):
            return Decision.ALLOW, "matched an allow rule"
        if action.kind is ActionKind.FILE_READ:
            return self.default_read, "default read policy"
        return self.default, "default policy"


def _merge(base: dict, extra: dict) -> dict:
    result = {**base}
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        elif isinstance(value, list) and isinstance(result.get(key), list):
            result[key] = result[key] + value
        else:
            result[key] = dict(value) if isinstance(value, dict) else value
    return result


__all__ = ["Policy", "RuleSet"]
