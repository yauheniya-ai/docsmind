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


def price_of(model: str) -> tuple[float, float] | None:
    return lookup(MODEL_PRICES, model)


def cost_breakdown(by_model: dict[str, Usage]) -> dict:
    """-> {"usd": total of the priced models, "complete": every model had a price, "models": [...]}.
    Cached input is billed at the full input price, so the figure is an upper bound when the
    provider discounts cached tokens."""
    models = []
    total = 0.0
    complete = True
    for model, usage in sorted(by_model.items()):
        price = price_of(model)
        cost = None
        if price is not None:
            cost = (usage.input_tokens * price[0] + usage.output_tokens * price[1]) / 1_000_000
            total += cost
        else:
            complete = False
        models.append(
            {
                "model": model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "input_price": price[0] if price else None,
                "output_price": price[1] if price else None,
                "usd": cost,
            }
        )
    return {"usd": total, "complete": complete, "models": models}


def usd(value: float) -> str:
    """$0.0912 for small sums, $12.34 from one dollar up."""
    return f"${value:,.2f}" if value >= 1 else f"${value:.4f}"
