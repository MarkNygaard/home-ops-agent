"""Application configuration from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Claude API
    anthropic_client_id: str = ""
    anthropic_client_secret: str = ""
    anthropic_api_key: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://home_ops_agent:password@localhost:5432/home_ops_agent"

    # GitHub
    github_token: str = ""
    github_repo: str = "MarkNygaard/home-ops"

    # Cluster
    cluster_domain: str = "mnygaard.io"
    ntfy_url: str = "http://ntfy.monitoring.svc.cluster.local"
    ntfy_alertmanager_topic: str = "alertmanager"
    ntfy_gatus_topic: str = "gatus"
    ntfy_agent_topic: str = "home-ops-agent"

    # Web UI
    session_secret: str = "change-me-in-production"
    base_url: str = "https://agent.mnygaard.io"

    # Agent behavior
    pr_check_interval_seconds: int = 300  # 5 minutes
    alert_cooldown_seconds: int = 900  # 15 minutes
    claude_model: str = "claude-sonnet-4-20250514"

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
