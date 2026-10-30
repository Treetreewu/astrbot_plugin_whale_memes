"""Framework-independent defaults, with optional operator-owned JSON overrides."""
from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class Config:
    enabled: bool = True
    ttl_days: int = 30
    max_notes_per_scope: int = 500
    max_notes_total: int = 5000
    max_searches: int = 2
    max_results: int = 3
    max_pages: int = 2
    max_concurrent: int = 2
    learning_per_hour: int = 6
    timeout_seconds: int = 45
    operation_timeout_seconds: int = 12
    max_source_chars: int = 8000
    allow_model_discovery: bool = True
    allow_search_excerpt: bool = True
    storage_path: str = ""
    model_task: str = "learner"
    provider_id: str = ""
    maibot_persona: str = "maibot-native-v1"

    @classmethod
    def parse(cls, values=None):
        values = dict(values or {})
        if set(values) - {f.name for f in fields(cls)}:
            raise ValueError("Unknown meme learning configuration field")
        defaults = asdict(cls())
        for name, value in values.items():
            if type(value) is not type(defaults[name]):
                raise ValueError("Invalid type for " + name)
        cfg = cls(**values)
        limits = {"ttl_days": (1, 365), "max_notes_per_scope": (1, 5000),
                  "max_notes_total": (1, 50000), "max_searches": (1, 2),
                  "max_results": (1, 5), "max_pages": (0, 3), "max_concurrent": (1, 4),
                  "learning_per_hour": (1, 60), "timeout_seconds": (5, 50),
                  "operation_timeout_seconds": (1, 30), "max_source_chars": (100, 16000)}
        for name, (low, high) in limits.items():
            if not low <= getattr(cfg, name) <= high:
                raise ValueError("Out of range: " + name)
        if cfg.max_notes_total < cfg.max_notes_per_scope:
            raise ValueError("Total capacity must cover one scope")
        if not cfg.model_task.strip() or not cfg.maibot_persona.strip():
            raise ValueError("Model task and MaiBot persona must be nonempty")
        return cfg

    @classmethod
    def load(cls, native=None):
        values = dict(native or {})
        override = os.environ.get("WHALE_MEMES_CONFIG")
        if override:
            extra = json.loads(Path(override).read_text(encoding="utf-8-sig"))
            if not isinstance(extra, dict):
                raise ValueError("Meme configuration must be a JSON object")
            values.update(extra)
        return cls.parse(values)
