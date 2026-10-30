from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DatasetImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    name: str = Field(min_length=1)
    tasks: list[dict[str, Any]] = Field(min_length=1)


class AgentConfigCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    name: str = Field(min_length=1)
    adapter: Literal["scripted", "llm"] = "scripted"
    settings: dict[str, Any] = Field(default_factory=dict)


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version_id: str
    agent_config_ids: list[str] = Field(min_length=1)
    split: Literal["development", "validation", "test", "all"] = "all"
    repetitions: int = Field(default=1, ge=1, le=20)


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_ids: list[str] = Field(min_length=2, max_length=2)

