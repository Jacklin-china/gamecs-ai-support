from functools import lru_cache
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
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "gamecs"
    minio_secure: bool = False


@lru_cache
def settings() -> Settings:
    return Settings()
