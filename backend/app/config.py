from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://converse:converse@localhost:5433/converse"

    ollama_url: str = "http://localhost:11434"
    agent_model: str = "qwen2.5:7b-instruct"
    router_enabled: bool = True
    embed_model: str = "nomic-embed-text"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_enabled: bool = True
    agent_max_steps: int = 6
    # Models served with app-side prompt rendering (exactly the fine-tuning format) via Ollama's raw mode.
    raw_prompt_models: str = "converse-1.5b"
    llm_timeout_s: float = 120.0

    aws_endpoint_url: str | None = "http://localhost:4566"
    aws_region: str = "ap-south-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    order_events_queue: str = "order-events"
    reminders_queue: str = "reminders"
    artifacts_bucket: str = "converse-artifacts"
    scheduler_role_arn: str = "arn:aws:iam::000000000000:role/converse-scheduler"

    timezone: str = "Asia/Kolkata"
    # Freezes "today" for reproducible evals / demos (ISO date). Empty = real clock.
    frozen_today: str = ""

    max_quantity_without_confirm: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()


def now() -> datetime:
    s = get_settings()
    tz = ZoneInfo(s.timezone)
    if s.frozen_today:
        d = date.fromisoformat(s.frozen_today)
        return datetime(d.year, d.month, d.day, 12, 0, tzinfo=tz)
    return datetime.now(tz)


def today() -> date:
    return now().date()
