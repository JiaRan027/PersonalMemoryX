from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransferProfile(BaseModel):
    """Governed metadata for reusing an experience outside its project."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["project", "cross_project"] = "project"
    problem_shape: str = Field(min_length=1, max_length=500)
    principle: str = Field(min_length=1, max_length=500)
    applies_when: list[str] = Field(default_factory=list, max_length=8)
    avoid_when: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("problem_shape", "principle")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("applies_when", "avoid_when")
    @classmethod
    def normalize_conditions(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values))
        if any(not value or len(value) > 240 for value in normalized):
            raise ValueError("conditions must contain 1 to 240 characters")
        return normalized

    def retrieval_text(self) -> str:
        return " ".join(
            [self.problem_shape, self.principle, *self.applies_when, *self.avoid_when]
        )
