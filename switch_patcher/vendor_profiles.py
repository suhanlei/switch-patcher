import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TEMPLATES_DIR = Path(__file__).parent.parent / "vendor_templates"

VENDOR_ALIASES = {
    "h3c": "h3c",
    "new_h3c": "h3c",
    "hp": "h3c",
    "huawei": "huawei",
    "ce": "huawei",
    "ruijie": "ruijie",
    "rg": "ruijie",
}


@dataclass
class CheckCommand:
    command: str
    key: str


@dataclass
class ActivateCommand:
    command: str
    description: str = ""


@dataclass
class VendorProfile:
    vendor: str
    netmiko_type: str
    remote_dir: str
    pre_check: list[CheckCommand]
    activate: list[ActivateCommand]
    post_check: list[CheckCommand]
    rollback: list[ActivateCommand]
    save: str
    patch_id_pattern: str
    error_patterns: list[str]
    md5_command: str


def _parse_check_list(items: list[dict]) -> list[CheckCommand]:
    return [CheckCommand(command=i["command"], key=i["key"]) for i in items]


def _parse_activate_list(items: list[dict]) -> list[ActivateCommand]:
    return [ActivateCommand(command=i["command"], description=i.get("description", "")) for i in items]


def load_profile(vendor: str, templates_dir: Path | None = None) -> VendorProfile:
    normalized = VENDOR_ALIASES.get(vendor.lower(), vendor.lower())
    tdir = templates_dir or TEMPLATES_DIR
    yaml_path = tdir / f"{normalized}.yaml"

    if not yaml_path.exists():
        raise FileNotFoundError(f"Vendor template not found: {yaml_path}")

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return VendorProfile(
        vendor=data["vendor"],
        netmiko_type=data["netmiko_type"],
        remote_dir=data["remote_dir"],
        pre_check=_parse_check_list(data["pre_check"]),
        activate=_parse_activate_list(data["activate"]),
        post_check=_parse_check_list(data["post_check"]),
        rollback=_parse_activate_list(data["rollback"]),
        save=data["save"],
        patch_id_pattern=data["patch_id_pattern"],
        error_patterns=data["error_patterns"],
        md5_command=data["md5_command"],
    )


def format_command(template: str, patch_file: str = "", patch_id: str = "") -> str:
    return template.replace("{patch_file}", patch_file).replace("{patch_id}", patch_id)
