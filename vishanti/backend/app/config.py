"""Runtime configuration. Every value can be overridden with an environment variable."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Data stores -------------------------------------------------------
    DATABASE_URL: str = "postgresql+asyncpg://postgres@localhost:5432/vishanti"
    # Owner connection used only by init_db and the tamper demo. The app
    # itself should connect as a role that does not own the tables.
    MIGRATE_DATABASE_URL: str | None = None
    # Analytics reads go here. Defaults to the primary when no replica exists.
    READ_DATABASE_URL: str | None = None
    # Set when connecting through PgBouncer in transaction-pooling mode:
    # asyncpg's prepared-statement cache must be disabled there.
    USE_PGBOUNCER: bool = False
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Auth ----------------------------------------------------------------
    JWT_SECRET: str = "dev-only-secret-change-me-0123456789abcdef"
    JWT_ALGORITHM: str = "HS256"
    BCRYPT_ROUNDS: int = 12
    ACCESS_TTL_MINUTES: int = 15
    REFRESH_TTL_DAYS: int = 7

    # --- SLA clock -----------------------------------------------------------
    # Length of one "SLA hour" in real seconds. 3600 in production.
    # For a live demo set it to 60 so a 48-hour SLA expires in 48 minutes.
    SLA_HOUR_SECONDS: int = 3600
    RESOLUTION_CONFIRM_HOURS: int = 72
    # Warn petitioner and officials when this fraction of the SLA is left.
    SLA_WARN_FRACTION: float = 0.2

    # --- Attachments -----------------------------------------------------------
    STORAGE_BACKEND: str = "local"  # "local" or "s3" (MinIO / AWS S3)
    LOCAL_STORAGE_DIR: str = "./storage"
    S3_ENDPOINT_URL: str | None = None  # how the API reaches MinIO
    S3_PUBLIC_ENDPOINT_URL: str | None = None  # how the browser reaches MinIO
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_BUCKET: str = "vishanti"
    S3_REGION: str = "us-east-1"
    MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024
    PUBLIC_API_URL: str = "http://localhost:8000"

    # --- Duplicate clustering --------------------------------------------------
    EMBEDDINGS_BACKEND: str = "auto"  # "auto" | "model" | "hashing"
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    SENTIMENT_MODEL: str = "distilbert-base-uncased-finetuned-sst-2-english"
    CLUSTER_MAX_DISTANCE: float = 0.30  # cosine distance; 0.30 == similarity 0.70
    SYSTEMIC_CLUSTER_SIZE: int = 3

    # --- Transparency ----------------------------------------------------------
    SUPPRESSION_MIN: int = 5
    DASHBOARD_CACHE_SECONDS: int = 120

    # --- Integrity ---------------------------------------------------------------
    VERIFY_INTERVAL_SECONDS: int = 300
    # Optional external anchor for daily checkpoints (a public GitHub gist).
    GITHUB_TOKEN: str | None = None
    CHECKPOINT_GIST_ID: str | None = None
    OMBUDSPERSON_CONTACT: str = "ombudsperson@example.edu"

    # --- HTTP ----------------------------------------------------------------
    CORS_ORIGINS: str = "http://localhost:3000"
    ENABLE_RATE_LIMIT: bool = True

    # Celery runs tasks eagerly in tests.
    CELERY_ALWAYS_EAGER: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
