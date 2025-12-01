from functools import lru_cache
import os
from dotenv import load_dotenv, find_dotenv

# Load environment variables from .env file with override
load_dotenv(find_dotenv(), override=True)


class Settings:
    def __init__(self):
        self.app_name: str = os.environ.get("APP_NAME", "SageAI Universal")
        self.environment: str = os.environ.get("ENVIRONMENT", "development")
        self.debug: bool = os.environ.get("DEBUG", "True").lower() == "true"
        self.host: str = os.environ.get("HOST", "0.0.0.0")
        self.port: int = int(os.environ.get("PORT", "8000"))
        self.base_url: str = os.environ.get("BASE_URL", "http://localhost:8000")
        
        # Security
        self.secret_key: str = os.environ.get("SECRET_KEY")
        if not self.secret_key:
            raise ValueError("SECRET_KEY environment variable is required")
            
        self.secret_value: str = os.environ.get("SECRET_VALUE")
        if not self.secret_value:
            raise ValueError("SECRET_VALUE environment variable is required")
            
        self.algorithm: str = os.environ.get("ALGORITHM", "HS256")
        self.access_token_expire_minutes: int = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))


@lru_cache()
def get_settings() -> Settings:
    return Settings()