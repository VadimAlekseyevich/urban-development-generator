from core.urban_generator.models.config import GenerationConfig


def test_generation_config_preserves_seed() -> None:
    config = GenerationConfig(seed=42, target_population=25_000)
    assert config.seed == 42
    assert config.target_population == 25_000
    assert "education" in config.infrastructure_categories
