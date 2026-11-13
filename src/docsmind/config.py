"""All configuration is env-driven. Nothing here imports an SDK directly —
adapters read the fields they need and raise a clear error if a required one
is missing (e.g. `DOCSMIND_ANTHROPIC_API_KEY` unset but the anthropic adapter
was selected).
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The model that captions images and reads pages when `vision_model` is not set.
DEFAULT_VISION_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-6-luna"}


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
    llm_model: str = "claude-sonnet-5-5"
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-large"
    # text-embedding-3-large's native dimensionality is 3072. OpenAI lets you
    # shorten it via the API's `dimensions` param (down to 256) at a small
    # quality cost if 3072-dim storage/index size becomes a concern — if you
    # do that, set this to match, since it drives the `vector(N)` column.
    embedding_dimensions: int = 3072
    vision_provider: str = "anthropic"  # anthropic | openai
    # Model used to caption images and read pages. Unset = claude-sonnet-5-5 (anthropic) or gpt-6-luna (openai).
    vision_model: str | None = None

    anthropic_api_key: str | None = None
    # Set both a key and a base URL when the provider is reached through a company gateway or proxy.
    # OpenAI-compatible base URLs usually end in /v1; Anthropic ones do not (the SDK adds /v1/messages).
    anthropic_base_url: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # OpenAI-compatible gateways / local servers
    # Passed to OpenAI reasoning models (none | minimal | low | medium | high).
    # Some of them reject function tools on chat-completions unless this is
    # "none"; the adapter falls back to "none" automatically when that happens.
    openai_reasoning_effort: str | None = None

    # --- agent (the Mind) ---
    # Cheaper/faster model for the orchestrator, summarizer and memory writer.
    # Falls back to `llm_model` when unset.
    agent_fast_model: str | None = None
    agent_max_steps: int = 8  # tool-loop iterations per turn (executor)
    agent_max_output_tokens: int = 2048
    agent_max_parallel_tools: int = 4
    agent_tool_timeout_s: float = 60.0
    agent_tool_result_max_chars: int = 12_000
    agent_context_budget_tokens: int = 24_000  # loop context before old tool results are elided
    agent_history_budget_tokens: int = 6_000  # verbatim chat history before it is summarized
    agent_recent_turns: int = 4  # user/assistant pairs always kept verbatim
    agent_memory_enabled: bool = True

    # --- web access (Change Analyzer only) ---
    # Tavily API key (https://tavily.com). Unset = the `web_search` tool is not offered; `fetch_url`
    # (read one public web page) needs no key and stays available.
    web_search_api_key: str | None = None
    web_timeout_s: float = 15.0  # per web request
    web_fetch_max_bytes: int = 2_000_000  # page bytes read before giving up

    # --- change analyzer ---
    # Share of the current version's sentences that changed. Below it the scan model sees the
    # current version plus a bounded diff; at or above it, both versions in full.
    change_diff_threshold: float = 0.25
    # Long-context model for the scan (one call, no tools). Unset = `llm_model`.
    change_scan_model: str | None = None
    # Token budget of one scan call (a 250K window): current version plus the other side (diff or
    # previous version) is capped at `max_input`, leaving the rest for the table. Longer documents are split
    # into several calls, cutting the current version at about `split` tokens at a section boundary.
    change_scan_max_input_tokens: int = 180_000
    change_scan_split_tokens: int = 90_000
    change_scan_max_output_tokens: int = 32_000  # per call; a cut-off call is resumed automatically
    change_scan_max_retries: int = 3  # relaunches of a failed or cut-off scan call
    change_verify_parallel: int = 4  # rows verified at the same time
    change_verify_max_steps: int = 8  # tool-loop steps per row
    # A verification that fails for a technical reason (the model gave no usable answer, a call
    # broke) is relaunched this many times before the row is left unverified.
    change_verify_max_retries: int = 3
    change_changelog_max_steps: int = 8  # tool-loop steps for the changelog search
    change_vision_dpi: int = 130  # resolution of page images for `page_vision`
    change_vision_max_output_tokens: int = 1_500  # per `page_vision` answer
    change_table_max_bytes: int = 10_000_000  # largest spreadsheet `load_table` downloads

    # --- ingestion ---
    chunk_size_tokens: int = 700
    chunk_overlap_tokens: int = 100
    caption_image_min_bytes: int = 2_000  # skip captioning tiny icons/spacers

    # --- on-demand enrichment (embeddings + image captions) ---
    # Cost estimates use a built-in price table (USD per 1M tokens) that can go
    # stale. Set these to override it for your models/contract; they only affect
    # the estimate shown before you confirm, never what you are actually billed.
    embedding_price_per_million: float | None = None
    vision_input_price_per_million: float | None = None
    vision_output_price_per_million: float | None = None
    vision_max_output_tokens: int = 300  # cap per caption
    embedding_batch_size: int = 64  # texts per embeddings request

    # --- tracing (optional) ---
    tracing_backend: str | None = None  # None | mlflow | langfuse

    @field_validator("openai_base_url", "anthropic_base_url", mode="before")
    @classmethod
    def _blank_base_url_is_unset(cls, value):
        """`DOCSMIND_OPENAI_BASE_URL=` (left empty in .env) means the provider's default endpoint."""
        if isinstance(value, str):
            value = value.strip().rstrip("/")
        return value or None

    @property
    def resolved_vision_model(self) -> str:
        """`vision_model`, or the default of the vision provider."""
        return self.vision_model or DEFAULT_VISION_MODELS.get(self.vision_provider, "")

    def is_managed_postgres(self) -> bool:
        return any(h in self.database_url for h in ("neon.tech", "supabase.co"))