"""Bounded activity signals for provider-disclosed model reasoning.

Provider reasoning is never copied into chat history. A provider may send a
separate reasoning field; this module reports that observable activity without
implying access to thoughts the provider did not disclose. An explicitly
enabled UI may show a short excerpt, which should stay ephemeral.
"""

from __future__ import annotations

import re


_CREDENTIAL = re.compile(
    r"(?i)(?:\b(?:api[_ -]?key|access[_ -]?token|secret|password)\s*[:=]\s*\S+"
    r"|\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|AIza[A-Za-z0-9_-]{12,})\b)"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_TOOL_MARKUP = re.compile(r"<\s*/?\s*(?:tool_call|function|parameter)\b[^>]*>", re.I)


class ReasoningActivity:
    """Turn streamed reasoning fragments into sparse, honest UI events."""

    def __init__(self, interval: int = 256, include_excerpt: bool = False):
        if interval < 1:
            raise ValueError("interval must be positive")
        self.interval = interval
        self.include_excerpt = bool(include_excerpt)
        self.characters = 0
        self._next_report = 1
        self._excerpt = ""

    def feed(self, fragment):
        """Return an activity payload when a new reporting threshold is crossed.

        A reasoning field may include implementation details, credentials
        quoted from context, or partial tool syntax. Raw text is excluded by
        default; opt-in excerpts are bounded and best-effort filtered, not a
        confidentiality guarantee. Character counts are observations, not
        token usage or evidence of model quality.
        """
        if not isinstance(fragment, str) or not fragment:
            return None
        self.characters += len(fragment)
        if self.include_excerpt and len(self._excerpt) < 512:
            self._excerpt += fragment[:512 - len(self._excerpt)]
        if self.characters < self._next_report:
            return None
        self._next_report = ((self.characters // self.interval) + 1) * self.interval
        event = {"source": "provider", "active": True,
                "characters": self.characters,
                "label": "Model reasoning activity"}
        if self.include_excerpt:
            excerpt = _TOOL_MARKUP.sub("[tool markup]", self._excerpt)
            excerpt = _CREDENTIAL.sub("[credential redacted]", excerpt)
            excerpt = _CONTROL.sub(" ", excerpt).strip()
            event["excerpt"] = excerpt[:240]
        return event

    def finish(self):
        if not self.characters:
            return None
        return {"source": "provider", "active": False,
                "characters": self.characters,
                "label": "Model reasoning received"}
