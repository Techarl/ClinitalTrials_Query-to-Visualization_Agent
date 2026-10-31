"""Typed local configuration. Credentials must never be included in API responses."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    openai_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    openai_base_url: HttpUrl = "https://api.openai.com/v1"
    openai_model: str = Field(default="gpt-6.1-sol", pattern=r"^[a-zA-Z0-9._-]+$")
    openai_reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    openai_max_output_tokens: int = Field(default=32768, ge=1, le=128000)
    openai_timeout_seconds: float = Field(default=120, gt=0)

    openrouter_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    openrouter_decisions_url: HttpUrl = "https://openrouter.ai/api/alpha/decisions"
    openrouter_decision_model: str = "typesafe/jev-1.14"
    enable_decision_experiment: bool = False
    enable_harness_experiment: bool = False
    harness_version: Literal["baseline-v1"] = "baseline-v1"

    neo4j_uri: str = Field(default="bolt://127.0.0.1:7687", pattern=r"^(bolt|neo4j)(\+s|\+ssc)?://")
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr = Field(default=SecretStr(""), repr=False)
    neo4j_database: str = "neo4j"

    app_data_dir: Path = Path("data")
    app_timezone: str = "Asia/Shanghai"
    ctgov_base_url: HttpUrl = "https://clinicaltrials.gov/api/v2"
    ctgov_page_size: int = Field(default=250, ge=1, le=1000)
    max_source_records: int = Field(default=5000, ge=1, le=50000)
    max_source_pages: int = Field(default=100, ge=1)
    source_timeout_seconds: float = Field(default=30, gt=0)
    source_retries: int = Field(default=2, ge=0, le=4)
    snapshot_ttl_seconds: int = Field(default=86400, ge=0)
    max_run_seconds: float = Field(default=300, gt=0)
    max_model_calls: int = Field(default=2, ge=1, le=4)
    max_context_chars: int = Field(default=40000, ge=8000)
    evidence_preview_limit: int = Field(default=3, ge=0, le=20)
    network_max_edges: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def require_baseline(self) -> Self:
        if self.enable_decision_experiment or self.enable_harness_experiment:
            raise ValueError("Experiments are not implemented; both experiment flags must remain false.")
        return self

    @property
    def data_directory(self) -> Path:
        return (PROJECT_ROOT / self.app_data_dir).resolve()
