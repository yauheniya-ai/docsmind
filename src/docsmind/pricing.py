"""Every price in the app, in one place: models, embeddings, and the cost helpers built on them.

Used by the enrichment estimates (embeddings, image captions) and by the cost line of a Change
Analyzer report. The tables are built in and will drift over time; the embedding and vision prices
can be overridden from config (`DOCSMIND_EMBEDDING_PRICE_PER_MILLION`, ...), and an unknown model
yields a cost of `None` ("price unknown") rather than a wrong number.

Prices are USD per 1M tokens.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from docsmind.domain.agent import Usage

# The one embedding model of the app.
EMBEDDING_PRICES: dict[str, float] = {
    "text-embedding-3-large": 0.13,
}

# Every chat and vision model of the app: model prefix -> (input $/1M tokens, output $/1M tokens).
# Longest prefix wins. "claude-sonnet-5" matches both spellings of the model id (5-5 and 5.5).
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-6.1-sol": (2.00, 10.00),
    "gpt-6.1-terra": (1.00, 5.00),
    "gpt-6-luna": (0.10, 0.50),
    "claude-sonnet-5": (2.00, 10.00),
}

# Tokens in the captioning instruction, and a plausible range for the reply.
CAPTION_PROMPT_TOKENS = 110
CAPTION_OUTPUT_TOKENS_LOW = 60


def lookup(table: dict, model: str):
    """Price entry for `model` by longest matching prefix, or None."""
    name = model.lower()
    best = None
    for prefix in table:
        if name.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return table[best] if best else None


def count_tokens(text: str) -> int:
    """Token count via tiktoken's cl100k_base (what text-embedding-3 uses);
    falls back to ~4 characters per token when tiktoken or its encoding file
    isn't available (e.g. offline on first use)."""
    if not text:
        return 0
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text, disallowed_special=()))
    except Exception:  # noqa: BLE001 - estimates are best-effort
        return max(1, len(text) // 4)


def image_tokens(width: int, height: int, *, provider: str) -> int:
    """Approximate input tokens one image costs, from its pixel size."""
    width, height = max(1, width), max(1, height)
    if provider == "anthropic":
        # Images are downscaled so the long edge is <= 1568 px and the area is
        # <= ~1.15 megapixels; cost is roughly width*height / 750.
        scale = min(1.0, 1568 / max(width, height), math.sqrt(1_150_000 / (width * height)))
        return max(1, round((width * scale) * (height * scale) / 750))

    # OpenAI "high detail": fit in 2048x2048, then shortest side to 768, then count 512 px tiles.
    scale = min(1.0, 2048 / max(width, height))
    w, h = width * scale, height * scale
    shrink = min(1.0, 768 / min(w, h))
    w, h = w * shrink, h * shrink
    tiles = math.ceil(w / 512) * math.ceil(h / 512)
    return 85 + 170 * tiles


# -- estimates shown before an on-demand enrichment ------------------------------------------------------


@dataclass
class Estimate:
    kind: str  # "embeddings" | "captions"
    provider: str
    model: str
    items: int  # things that will be processed
    already_done: int = 0
    skipped: int = 0  # captions only: too small to be worth captioning
    input_tokens: int = 0
    output_tokens_low: int = 0
    output_tokens_high: int = 0
    cost_usd_low: float | None = None
    cost_usd_high: float | None = None
    price_known: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "provider": self.provider,
            "model": self.model,
            "items": self.items,
            "already_done": self.already_done,
            "skipped": self.skipped,
            "input_tokens": self.input_tokens,
            "output_tokens_low": self.output_tokens_low,
            "output_tokens_high": self.output_tokens_high,
            "cost_usd_low": self.cost_usd_low,
            "cost_usd_high": self.cost_usd_high,
            "price_known": self.price_known,
            "note": self.note,
        }


def embedding_estimate(
    *, provider: str, model: str, items: int, tokens: int, already_done: int, price_override: float | None
) -> Estimate:
    price = price_override if price_override is not None else lookup(EMBEDDING_PRICES, model)
    cost = tokens / 1_000_000 * price if price is not None else None
    return Estimate(
        kind="embeddings",
        provider=provider,
        model=model,
        items=items,
        already_done=already_done,
        input_tokens=tokens,
        cost_usd_low=cost,
        cost_usd_high=cost,
        price_known=price is not None,
        note=(
            "Token count is exact for this model's tokenizer; the price comes from a built-in table."
            if price is not None
            else f"No built-in price for {model}. Set DOCSMIND_EMBEDDING_PRICE_PER_MILLION to see a cost."
        ),
    )


def caption_estimate(
    *,
    provider: str,
    model: str,
    items: int,
    skipped: int,
    already_done: int,
    image_input_tokens: int,
    input_price_override: float | None,
    output_price_override: float | None,
    max_output_tokens: int,
) -> Estimate:
    input_tokens = image_input_tokens + CAPTION_PROMPT_TOKENS * items
    out_low = CAPTION_OUTPUT_TOKENS_LOW * items
    out_high = max_output_tokens * items
    table = lookup(MODEL_PRICES, model)
    in_price = input_price_override if input_price_override is not None else (table[0] if table else None)
    out_price = output_price_override if output_price_override is not None else (table[1] if table else None)
    known = in_price is not None and out_price is not None
    low = high = None
    if known:
        assert in_price is not None and out_price is not None
        low = (input_tokens * in_price + out_low * out_price) / 1_000_000
        high = (input_tokens * in_price + out_high * out_price) / 1_000_000
    return Estimate(
        kind="captions",
        provider=provider,
        model=model,
        items=items,
        already_done=already_done,
        skipped=skipped,
        input_tokens=input_tokens,
        output_tokens_low=out_low,
        output_tokens_high=out_high,
        cost_usd_low=low,
        cost_usd_high=high,
        price_known=known,
        note=(
            "Image tokens are approximated from pixel size and the reply length is a range, so the real "
            "cost can differ."
            if known
            else (
                f"No built-in price for {model}. Set DOCSMIND_VISION_INPUT_PRICE_PER_MILLION and "
                "DOCSMIND_VISION_OUTPUT_PRICE_PER_MILLION to see a cost."
            )
        ),
    )


# -- what a run of chat models cost ------------------------------------------------------------------------

# Cache prices as a multiple of the input price, for providers that publish a fixed ratio (Anthropic:
# a cache read costs 0.1x, a 5-minute cache write 1.25x). Models without an entry have no built-in
# cached price: it falls back to the input price and the cost is marked as an upper bound. Override
# any price with `DOCSMIND_MODEL_PRICES` (see config.py).
CACHE_MULTIPLIERS: dict[str, tuple[float, float]] = {
    "claude-": (0.1, 1.25),
}


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens. `*_known` is False when the value is the input-price fallback."""

    input: float
    cached_read: float
    cache_write: float
    output: float
    cached_read_known: bool = False
    cache_write_known: bool = False


def price_of(model: str) -> tuple[float, float] | None:
    """(input, output) list price of `model`, or None. Longest prefix wins."""
    return lookup(MODEL_PRICES, model)


def model_price(model: str, overrides: dict[str, dict[str, float]] | None = None) -> ModelPrice | None:
    """The four prices of `model`: built-in table, then the configured overrides (longest prefix)."""
    base = price_of(model)
    given = lookup(overrides, model) if overrides else None
    given = given or {}
    price_in = given.get("input", base[0] if base else None)
    price_out = given.get("output", base[1] if base else None)
    if price_in is None or price_out is None:
        return None
    mult = lookup(CACHE_MULTIPLIERS, model)
    read = given.get("cached_read", price_in * mult[0] if mult else None)
    write = given.get("cache_write", price_in * mult[1] if mult else None)
    return ModelPrice(
        input=price_in,
        cached_read=read if read is not None else price_in,
        cache_write=write if write is not None else price_in,
        output=price_out,
        cached_read_known=read is not None,
        cache_write_known=write is not None,
    )


def _cost(usage: Usage, price: ModelPrice) -> tuple[float, float]:
    """-> (cost with caching, cost if nothing had been cached), USD."""
    actual = (
        usage.uncached_input_tokens * price.input
        + usage.cached_input_tokens * price.cached_read
        + usage.cache_write_tokens * price.cache_write
        + usage.output_tokens * price.output
    ) / 1_000_000
    plain = (usage.input_tokens * price.input + usage.output_tokens * price.output) / 1_000_000
    return actual, plain


def cost_breakdown(by_model: dict[str, Usage], overrides: dict[str, dict[str, float]] | None = None) -> dict:
    """What a run cost, split by model:

        cost = uncached*input + cached_read*cached_read + cache_write*cache_write + output*output

    -> {"usd", "usd_without_cache", "cache_hit_rate", "complete" (every model had a price),
        "upper_bound" (a cached or cache-write token was priced at the input price), "models": [...]}."""
    models = []
    total = plain_total = 0.0
    complete, upper_bound = True, False
    cached = input_total = 0
    for model, usage in sorted(by_model.items()):
        price = model_price(model, overrides)
        cost = plain = None
        bound = False
        if price is not None:
            cost, plain = _cost(usage, price)
            total += cost
            plain_total += plain
            bound = (usage.cached_input_tokens > 0 and not price.cached_read_known) or (
                usage.cache_write_tokens > 0 and not price.cache_write_known
            )
            upper_bound = upper_bound or bound
        else:
            complete = False
        cached += usage.cached_input_tokens
        input_total += usage.input_tokens
        models.append(
            {
                "model": model,
                "input_tokens": usage.input_tokens,
                "uncached_input_tokens": usage.uncached_input_tokens,
                "cached_read_tokens": usage.cached_input_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "output_tokens": usage.output_tokens,
                "input_price": price.input if price else None,
                "cached_read_price": price.cached_read if price else None,
                "cache_write_price": price.cache_write if price else None,
                "output_price": price.output if price else None,
                "usd": cost,
                "usd_without_cache": plain,
                "upper_bound": bound,
            }
        )
    return {
        "usd": total,
        "usd_without_cache": plain_total,
        "cache_hit_rate": cached / input_total if input_total else 0.0,
        "complete": complete,
        "upper_bound": upper_bound,
        "models": models,
    }


def cost_by_tool(
    by_tool_model: dict[tuple[str, str], Usage], overrides: dict[str, dict[str, float]] | None = None
) -> list[dict]:
    """USD per tool (summed over the models it called), most expensive first. Unpriced models add nothing."""
    out: dict[str, dict] = {}
    for (tool, model), usage in by_tool_model.items():
        entry = out.setdefault(tool, {"tool": tool, "usd": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0})
        entry["input_tokens"] += usage.input_tokens
        entry["output_tokens"] += usage.output_tokens
        price = model_price(model, overrides)
        if price is not None:
            entry["usd"] += _cost(usage, price)[0]
    return sorted(out.values(), key=lambda e: e["usd"], reverse=True)


def usd(value: float) -> str:
    """$0.0912 for small sums, $12.34 from one dollar up."""
    return f"${value:,.2f}" if value >= 1 else f"${value:.4f}"
