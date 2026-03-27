"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration for infra-agent, loaded from environment variables."""

    provider: str = "openrouter"
    api_key: str = ""
    model: str = "deepseek/deepseek-chat"
    context_lines: int = 20

    model_config = {"env_prefix": "INFRA_AGENT_"}
