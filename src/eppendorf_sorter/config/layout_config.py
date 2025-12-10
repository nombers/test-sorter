from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass
class SystemLayoutConfig:
    unloading_tripods: int
    loading_tripods: int
    racks_in_loading_zone: int
    racks_in_unloading_zone: int


def load_system_layout_config(path: Path | None = None) -> SystemLayoutConfig:
    if path is None:
        from pathlib import Path
        path = Path(__file__).with_name("layout.yaml")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return SystemLayoutConfig(
        unloading_tripods=raw["tripods"]["unloading"],
        loading_tripods=raw["tripods"]["loading"],
        racks_in_loading_zone=raw["racks"]["loading_zone"],
        racks_in_unloading_zone=raw["racks"]["unloading_zone"],
    )
