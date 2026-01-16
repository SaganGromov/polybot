from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr

class Settings(BaseSettings):
    WALLET_PRIVATE_KEY: SecretStr = Field(..., description="Wallet Private Key")
    DATABASE_URL: str = Field(..., description="PostgreSQL Database URL")
    DRY_RUN: bool = Field(default=True, description="If True, no real trades are executed")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
