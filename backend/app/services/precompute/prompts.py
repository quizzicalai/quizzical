"""§21 Phase 8 — versioned prompt registry (`AC-PRECOMP-QUAL-1`).

Every prompt template carries `(name, semver, sha256)`; the SHA is a
content-address over the rendered template body so two registrations of
the "same" semver with different bodies are detectable. `provenance(name)`
returns a JSON-serialisable dict suitable for embedding into
`topic_packs.model_provenance` and `precompute_jobs.evaluator_history`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class Prompt:
    name: str
    semver: str
    template: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.template.encode("utf-8")).hexdigest()

    def provenance(self) -> dict[str, str]:
        return {
            "name": self.name,
            "semver": self.semver,
            "sha256": self.sha256,
        }


class PromptRegistry:
    """In-process registry; populated at import time by feature modules."""

    def __init__(self) -> None:
        self._by_name: dict[str, Prompt] = {}

    def register(self, prompt: Prompt) -> Prompt:
        if not _SEMVER_RE.match(prompt.semver):
            raise ValueError(f"semver must be MAJOR.MINOR.PATCH, got {prompt.semver!r}")
        existing = self._by_name.get(prompt.name)
        if existing is not None and existing.semver == prompt.semver and existing.sha256 != prompt.sha256:
            raise ValueError(
                f"prompt {prompt.name!r} v{prompt.semver} already registered "
                f"with a different body — bump semver"
            )
        self._by_name[prompt.name] = prompt
        return prompt

    def get(self, name: str) -> Prompt:
        return self._by_name[name]

    def provenance(self, name: str) -> dict[str, str]:
        return self.get(name).provenance()

    def all(self) -> list[Prompt]:
        return list(self._by_name.values())


# Module-level singleton — feature code imports and uses this directly.
registry = PromptRegistry()
