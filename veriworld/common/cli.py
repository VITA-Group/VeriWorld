"""Configuration validation shared by public task launchers."""
import os
from pathlib import Path
from typing import Optional, Sequence
from .vlm import ModelConfig


def select_config(configs: Sequence[ModelConfig], name: Optional[str]) -> ModelConfig:
    """Select an exact configured label; never substitute another model."""
    if not configs:
        raise ValueError("The model configuration is empty")
    names = [config.name for config in configs]
    if len(names) != len(set(names)):
        raise ValueError("Model configuration names must be unique")
    if name is None:
        return configs[0]
    for config in configs:
        if config.name == name:
            return config
    raise ValueError(f"Unknown model configuration {name!r}. Available names: {', '.join(names)}")


def default_build() -> Optional[Path]:
    """An explicit executable path works with both Windows and Linux builds."""
    value = os.environ.get("VERIWORLD_BUILD")
    return Path(value).expanduser() if value else None
