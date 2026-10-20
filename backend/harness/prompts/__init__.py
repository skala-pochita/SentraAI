"""H1 프롬프트·정책 레지스트리: harness/prompts/*.yaml (id, version, system, user_template, output_schema).

get_prompt("probe_judge") → 최신 버전, get_prompt("probe_judge@v1") → 특정 버전. 실행 시 prompt_id@version 을
이벤트·트레이스에 기록한다.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from core.errors import AppError, ErrorCode

DIR = Path(__file__).resolve().parent


class PromptSpec(BaseModel):
    id: str
    version: int
    system: str
    user_template: str
    output_schema: str | None = None
    description: str = ""

    @property
    def ref(self) -> str:
        return f"{self.id}@v{self.version}"

    def render(self, **kwargs: Any) -> str:
        return self.user_template.format(**kwargs)

    def render_system(self, **kwargs: Any) -> str:
        """system 의 {이름} 자리표시자를 채운다. 없는 키·${...}·JSON 중괄호는 그대로, {{ }} 는 { } 로."""

        def sub(m: re.Match[str]) -> str:
            key = m.group(1)
            return str(kwargs[key]) if key in kwargs else m.group(0)

        text = self.system.replace("{{", "\x00").replace("}}", "\x01")
        text = re.sub(r"(?<!\$)\{([A-Za-z_][A-Za-z0-9_]*)\}", sub, text)
        return text.replace("\x00", "{").replace("\x01", "}")


@lru_cache
def _all() -> dict[str, PromptSpec]:
    out: dict[str, PromptSpec] = {}
    for p in sorted(DIR.glob("*.yaml")):
        spec = PromptSpec.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        out[spec.ref] = spec
    return out


def get_prompt(ref: str) -> PromptSpec:
    prompts = _all()
    if "@" in ref:
        if ref not in prompts:
            raise AppError(ErrorCode.NOT_FOUND, f"prompt {ref} not found")
        return prompts[ref]
    versions = [p for p in prompts.values() if p.id == ref]
    if not versions:
        raise AppError(ErrorCode.NOT_FOUND, f"prompt {ref} not found")
    return max(versions, key=lambda p: p.version)


def list_prompts() -> list[str]:
    return sorted(_all())
