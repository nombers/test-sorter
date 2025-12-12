from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass
class SystemLayoutConfig:
    unloading_rack: int
    loading_rack: int


def load_system_layout_config(path: Path | None = None) -> SystemLayoutConfig:
    if path is None:
        from pathlib import Path
        path = Path(__file__).with_name("layout.yaml")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return SystemLayoutConfig(
        unloading_rack=raw["rack"]["unloading"],
        loading_rack=raw["rack"]["loading"],
    )
