"""All configuration is env-driven. Nothing here imports an SDK directly —
adapters read the fields they need and raise a clear error if a required one
is missing (e.g. `DOCSMIND_ANTHROPIC_API_KEY` unset but the anthropic adapter
was selected).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class DocsmindConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCSMIND_", env_file=".env", extra="ignore")

    # --- storage ---
    # Matches docker-compose.yml's 5433 host port (chosen to avoid clashing
    # with a locally-installed Postgres, e.g. Homebrew's default on macOS).
    database_url: str = "postgresql+asyncpg://docsmind:docsmind@localhost:5433/docsmind"
    blob_backend: str = "filesystem"  # filesystem | s3 | supabase
    blob_root: str = "./.docsmind/blobs"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None  # set for R2 / Backblaze / MinIO
    supabase_url: str | None = None
    supabase_key: str | None = None
    supabase_bucket: str = "docsmind"

    # --- models ---
    llm_provider: str = "anthropic"  # anthropic | openai
    llm_model: str = "claude-sonnet-4-6"
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-large"
    # text-embedding-3-large's native dimensionality is 3072. OpenAI lets you
    # shorten it via the API's `dimensions` param (down to 256) at a small
    # quality cost if 3072-dim storage/index size becomes a concern — if you
    # do that, set this to match, since it drives the `vector(N)` column.
    embedding_dimensions: int = 3072
    vision_provider: str = "anthropic"

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # --- ingestion ---
    chunk_size_tokens: int = 700
    chunk_overlap_tokens: int = 100
    caption_image_min_bytes: int = 2_000  # skip captioning tiny icons/spacers

    # --- tracing (optional) ---
    tracing_backend: str | None = None  # None | mlflow

    def is_managed_postgres(self) -> bool:
        return any(h in self.database_url for h in ("neon.tech", "supabase.co"))