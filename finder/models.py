"""Plain data types returned by the engine. No web or database code here."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

Status = Literal["valid", "invalid", "catch_all", "unknown"]


@dataclass
class CheckResult:
    name: str  # "syntax", "provider", "mx", "smtp", "catch_all"
    passed: Optional[bool]  # None = skipped or inconclusive
    detail: str = ""


@dataclass
class SmtpResponse:
    stage: str  # "connect", "ehlo", "helo", "mail_from", "rcpt_to", "rcpt_to_random", "quit"
    code: int
    message: str


@dataclass
class VerificationResult:
    email: str
    domain: str
    status: Status
    confidence: int
    checks: list[CheckResult] = field(default_factory=list)
    mx_hosts: list[str] = field(default_factory=list)
    smtp: list[SmtpResponse] = field(default_factory=list)
    is_free: bool = False
    is_disposable: bool = False
    is_catch_all: Optional[bool] = None
    is_gibberish: bool = False  # local part looks machine-generated (xkq7zt@)
    reason: str = ""
    duration_ms: int = 0
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def check(self, name: str) -> Optional[CheckResult]:
        return next((c for c in self.checks if c.name == name), None)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationResult":
        data = dict(data)
        data["checks"] = [CheckResult(**c) for c in data.get("checks", [])]
        data["smtp"] = [SmtpResponse(**s) for s in data.get("smtp", [])]
        return cls(**data)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


Kind = Literal["personal", "role"]
SourceType = Literal["website", "pdf", "github", "websearch"]


@dataclass
class FoundEmail:
    """One sighting of an address in a public source."""
    email: str
    domain: str
    kind: Kind
    source_type: SourceType
    source_url: str
    full_name: Optional[str] = None  # person's name found near the address, if any
    name_source: Optional[str] = None  # "mailto-text", "same-element", "heading", "inline", "commit"
    context: str = ""  # short text snippet around the sighting
    found_at: str = field(default_factory=now_iso)
    title: Optional[str] = None  # job title found near the address
    department: Optional[str] = None  # see finder.titles.DEPARTMENTS
    seniority: Optional[str] = None  # "executive" | "senior" | "junior" | None
    last_seen: Optional[str] = None  # most recent search that saw this sighting (set by the store)


@dataclass
class TemplateScore:
    template: str
    support: int  # name+email pairs this template explains
    share: float  # fractional votes (ambiguous pairs split their vote)


@dataclass
class PatternResult:
    domain: str
    template: Optional[str]  # best template, None if no usable evidence
    support: int = 0
    contradict: int = 0
    total: int = 0  # name+email pairs considered
    confidence: int = 0
    breakdown: list[TemplateScore] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)  # {"name", "email", "templates": [...]}
    variant_support: int = 0  # examples using a collision fallback of the main format (see COLLISION_VARIANTS)
    variants: list[str] = field(default_factory=list)
    computed_at: str = field(default_factory=now_iso)

    @property
    def summary(self) -> str:
        if not self.template:
            return "no pattern: no name + email pairs found"
        extra = f" (+{self.variant_support} {'/'.join(self.variants)} when the name is taken)" if self.variant_support else ""
        return f"{self.template}, {self.support} of {self.total} examples{extra}, {self.confidence}%"
