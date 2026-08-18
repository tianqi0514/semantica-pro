from __future__ import annotations

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


_TYPE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class TypeDefinition(BaseModel):
    """A stable machine code plus a human-readable domain definition."""

    name: str
    label: str = ""
    description: str = ""
    aliases: List[str] = Field(default_factory=list)
    patterns: List[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not _TYPE_NAME_RE.fullmatch(normalized):
            raise ValueError("类型编码只能包含英文字母、数字和下划线，且必须以字母开头")
        return normalized

    def prompt_text(self) -> str:
        parts = [self.name]
        if self.label:
            parts.append(f"中文含义：{self.label}")
        if self.description:
            parts.append(f"定义：{self.description}")
        return "；".join(parts)


class ExtractionConfig(BaseModel):
    scenario_name: str = "通用文档抽取"
    method: Literal["llm", "regex", "ml"] = "llm"
    provider: str = "kimi"
    model: str = "kimi-k3"
    base_url: Optional[str] = "https://api.moonshot.cn/v1"
    api_key: Optional[SecretStr] = Field(default=None, exclude=True)
    entity_types: List[TypeDefinition]
    relation_types: List[TypeDefinition]
    entity_confidence: float = Field(0.55, ge=0.0, le=1.0)
    relation_confidence: float = Field(0.55, ge=0.0, le=1.0)
    chunk_size: int = Field(5000, ge=500, le=30000)
    chunk_overlap: int = Field(300, ge=0, le=5000)
    extract_temporal_bounds: bool = True

    @model_validator(mode="after")
    def validate_config(self) -> "ExtractionConfig":
        if self.provider.lower() == "kimi":
            self.provider = "openai"
            self.base_url = self.base_url or "https://api.moonshot.cn/v1"
            if not self.model or self.model == "gpt-4.1-mini":
                self.model = "kimi-k3"
        if self.provider.lower() == "xiaomi":
            self.provider = "openai"
            self.base_url = self.base_url or "https://api.xiaomimimo.com/v1"
            if not self.model or self.model == "gpt-4.1-mini":
                self.model = "mimo-v2.5"
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        entity_names = [item.name for item in self.entity_types]
        relation_names = [item.name for item in self.relation_types]
        if len(entity_names) != len(set(entity_names)):
            raise ValueError("实体类型编码不能重复")
        if len(relation_names) != len(set(relation_names)):
            raise ValueError("关系类型编码不能重复")
        if not entity_names:
            raise ValueError("至少需要配置一个实体类型")
        return self


class ScenarioTemplateWrite(BaseModel):
    """Editable product metadata plus the executable extraction configuration."""

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    category: str = Field(default="通用", min_length=1, max_length=80)
    enabled: bool = True
    config: ExtractionConfig

    @model_validator(mode="after")
    def align_scenario_name(self) -> "ScenarioTemplateWrite":
        self.name = self.name.strip()
        self.description = self.description.strip()
        self.category = self.category.strip()
        if not self.name:
            raise ValueError("模板名称不能为空")
        if not self.category:
            raise ValueError("模板分类不能为空")
        self.config = self.config.model_copy(update={"scenario_name": self.name})
        return self


class LLMSettingsUpdate(BaseModel):
    provider: Literal["kimi"] = "kimi"
    display_name: str = Field(default="Kimi K3", min_length=1, max_length=80)
    model: str = Field(default="kimi-k3", min_length=1, max_length=120)
    base_url: str = Field(default="https://api.moonshot.cn/v1", min_length=8, max_length=500)
    api_key: Optional[SecretStr] = Field(default=None, exclude=True)
    clear_api_key: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("https://", "http://")):
            raise ValueError("模型服务地址必须以 http:// 或 https:// 开头")
        return normalized


class ExtractionRequestSummary(BaseModel):
    job_id: str
    status: str
    result: dict
