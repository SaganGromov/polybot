from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr

class Settings(BaseSettings):
    WALLET_PRIVATE_KEY: SecretStr = Field(..., description="Wallet Private Key")

    DATABASE_URL: str = Field(..., description="PostgreSQL Database URL")
    DRY_RUN: bool = Field(default=False, description="If True, no real trades are executed")
    
    # AI Trade Analysis
    GEMINI_API_KEY: SecretStr | None = Field(default=None, description="Google Gemini API Key for AI trade analysis")

    model_config = SettingsConfigDict(env_file="polybot/.env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
