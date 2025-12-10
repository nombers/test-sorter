# src/eppendorf_sorter/config/loader_config.py
from dataclasses import dataclass
from pathlib import Path
import yaml


CONFIG_PATH = Path(__file__).with_name("loader.yaml")


@dataclass(frozen=True)
class LIS:
    host: str
    port: int
    
    
@dataclass(frozen=True)
class LoaderScannerConfig:
    ip: str
    port: int
    name: str
    timeout: float


@dataclass(frozen=True)
class LoaderConfig:
    ip: str                 # IP робота-загрузчика
    name: str               # имя робота (логическое)
    robot_program_name: str # имя программы на контроллере
    scanner: LoaderScannerConfig
    lis: LIS


def load_loader_config(path: Path | None = None) -> LoaderConfig:
    cfg_path = path or CONFIG_PATH
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    loader_raw = raw["loader"]
    scanner_raw = loader_raw["scanner"]
    lis_raw = loader_raw["lis"]

    scanner = LoaderScannerConfig(
        ip=scanner_raw["ip"],
        port=int(scanner_raw["port"]),
        name=scanner_raw["name"],
        timeout=float(scanner_raw["timeout"]),
    )
    
    lis = LIS(
        ip=lis_raw["ip"],
        port=int(lis_raw["port"])
    )
    
    return LoaderConfig(
        ip=loader_raw["ip"],
        name=loader_raw["name"],
        robot_program_name=loader_raw["robot_program_name"],
        scanner=scanner,
        lis=lis,
    )
