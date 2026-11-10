from docsmind.enrichment.pricing import (
    caption_estimate,
    embedding_estimate,
    image_tokens,
    resolve_vision_model,
)


def test_embedding_estimate_known_price() -> None:
    estimate = embedding_estimate(
        provider="openai",
        model="text-embedding-3-small",
        items=10,
        tokens=50_000,
        already_done=2,
        price_override=None,
    )
    assert estimate.price_known is True
    assert estimate.cost_usd_low == estimate.cost_usd_high
    assert estimate.cost_usd_low is not None and estimate.cost_usd_low > 0


def test_caption_estimate_unknown_price() -> None:
    estimate = caption_estimate(
        provider="anthropic",
        model="unknown-model-v1",
        items=3,
        skipped=1,
        already_done=0,
        image_input_tokens=1_200,
        input_price_override=None,
        output_price_override=None,
        max_output_tokens=300,
    )
    assert estimate.price_known is False
    assert estimate.cost_usd_low is None
    assert estimate.cost_usd_high is None


def test_image_tokens_increase_with_size() -> None:
    small = image_tokens(300, 200, provider="openai", model="gpt-4o")
    large = image_tokens(2000, 1200, provider="openai", model="gpt-4o")
    assert large > small


def test_default_vision_model_resolution() -> None:
    assert resolve_vision_model("openai", None) == "gpt-4o-mini"
    assert resolve_vision_model("anthropic", None).startswith("claude-")
