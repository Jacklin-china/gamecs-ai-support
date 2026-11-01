from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "mysql+pymysql://gamecs:gamecs@localhost:3306/gamecs_new?charset=utf8mb4"
    redis_url: str = "redis://localhost:6379/0"
    local_mode: bool = False
    local_auto_tasks: bool = True
    rate_limit_enabled: bool = True
    jwt_secret: str = ""
    jwt_minutes: int = 120
    cors_origins: str = "http://localhost:5173,http://localhost:5174"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    chat_provider: Literal["deepseek", "siliconflow"] = "deepseek"
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    siliconflow_chat_model: str = "THUDM/GLM-4-9B-0414"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "gamecs"
    minio_secure: bool = False

    @property
    def chat_api_key(self) -> str:
        return self.siliconflow_api_key if self.chat_provider == "siliconflow" else self.deepseek_api_key

    @property
    def chat_base_url(self) -> str:
        return self.siliconflow_base_url if self.chat_provider == "siliconflow" else self.deepseek_base_url

    @property
    def chat_model(self) -> str:
        return self.siliconflow_chat_model if self.chat_provider == "siliconflow" else self.deepseek_model


@lru_cache
def settings() -> Settings:
    return Settings()
