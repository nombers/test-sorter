# src/eppendorf_sorter/config/loader_config.py
from dataclasses import dataclass
from pathlib import Path
import yaml


CONFIG_PATH = Path(__file__).with_name("robot.yaml")


@dataclass(frozen=True)
class LIS:
    ip: str
    port: int
    
    
@dataclass(frozen=True)
class ScannerConfig:
    ip: str
    port: int
    name: str
    timeout: float


@dataclass(frozen=True)
class RobotcConfig:
    ip: str                 # IP робота-загрузчика
    name: str               # имя робота (логическое)
    robot_program_name: str # имя программы на контроллере
    scanner: ScannerConfig
    lis: LIS


def load_robot_config(path: Path | None = None) -> RobotcConfig:
    cfg_path = path or CONFIG_PATH
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    robot_raw = raw["robot"]
    scanner_raw = raw["scanner"]
    lis_raw = raw["lis"]

    scanner = ScannerConfig(
        ip=scanner_raw["ip"],
        port=int(scanner_raw["port"]),
        name=scanner_raw["name"],
        timeout=float(scanner_raw["timeout"]),
    )
    
    lis = LIS(
        ip=lis_raw["ip"],
        port=int(lis_raw["port"])
    )
    
    return RobotcConfig(
        ip=robot_raw["ip"],
        name=robot_raw["name"],
        robot_program_name=robot_raw["robot_program_name"],
        scanner=scanner,
        lis=lis,
    )
