from docsmind.config import DocsmindConfig
from docsmind.pricing import (
    MODEL_PRICES,
    caption_estimate,
    embedding_estimate,
    image_tokens,
)


def test_embedding_estimate_known_price() -> None:
    estimate = embedding_estimate(
        provider="openai",
        model="text-embedding-3-large",
        items=10,
        tokens=50_000,
        already_done=2,
        price_override=None,
    )
    assert estimate.price_known is True
    assert estimate.cost_usd_low == estimate.cost_usd_high == 50_000 / 1_000_000 * 0.13


def test_the_other_embedding_models_are_not_priced() -> None:
    estimate = embedding_estimate(
        provider="openai", model="text-embedding-3-small", items=1, tokens=1_000, already_done=0, price_override=None
    )
    assert estimate.price_known is False and estimate.cost_usd_low is None


def test_the_four_models_have_a_price_and_a_known_caption_cost() -> None:
    assert set(MODEL_PRICES) == {"gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna", "claude-sonnet-5"}
    for model in ("gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna", "claude-sonnet-5-5", "claude-sonnet-5.5"):
        estimate = caption_estimate(
            provider="openai",
            model=model,
            items=2,
            skipped=0,
            already_done=0,
            image_input_tokens=1_000,
            input_price_override=None,
            output_price_override=None,
            max_output_tokens=300,
        )
        assert estimate.price_known is True, model


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
    small = image_tokens(300, 200, provider="openai")
    large = image_tokens(2000, 1200, provider="openai")
    assert large > small


def test_default_vision_model_resolution() -> None:
    assert DocsmindConfig(vision_provider="openai", vision_model=None).resolved_vision_model == "gpt-6-luna"
    assert DocsmindConfig(vision_provider="anthropic", vision_model=None).resolved_vision_model == "claude-sonnet-5-5"
    assert DocsmindConfig(vision_provider="openai", vision_model="gpt-6.1-sol").resolved_vision_model == "gpt-6.1-sol"
