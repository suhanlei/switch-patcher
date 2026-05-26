"""
厂商命令模板加载模块
- 从YAML文件中读取各厂商（H3C/华为/锐捷）的补丁操作命令模板
- 提供命令占位符替换功能（{patch_file}、{patch_id}）
- 支持厂商别名映射，兼容不同写法
"""

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# YAML模板文件所在目录，与switch_patcher包同级
TEMPLATES_DIR = Path(__file__).parent.parent / "vendor_templates"

# 厂商别名映射：将各种写法统一为标准名称
VENDOR_ALIASES = {
    "h3c": "h3c",
    "new_h3c": "h3c",    # 新华三等同于H3C
    "hp": "h3c",         # HP Comware也用H3C模板
    "huawei": "huawei",
    "ce": "huawei",       # 华为CE系列使用华为模板
    "ruijie": "ruijie",
    "rg": "ruijie",      # 锐捷缩写
}


@dataclass
class CheckCommand:
    """健康检查命令，包含命令文本和键名（用于标识输出结果）"""
    command: str
    key: str


@dataclass
class ActivateCommand:
    """补丁激活/回退命令，包含命令文本和中文描述"""
    command: str
    description: str = ""


@dataclass
class VendorProfile:
    """厂商配置档案，包含该厂商补丁操作所需的全部命令和信息"""
    vendor: str                       # 厂商标准名称
    netmiko_type: str                 # netmiko设备类型标识
    remote_dir: str                   # 设备端补丁存放目录
    pre_check: list[CheckCommand]     # 补丁前健康检查命令列表
    activate: list[ActivateCommand]   # 补丁激活命令列表
    post_check: list[CheckCommand]    # 补丁后健康检查命令列表
    rollback: list[ActivateCommand]   # 回退命令列表
    save: str                         # 保存配置的命令
    patch_id_pattern: str             # 从输出中提取补丁版本号的正则表达式
    error_patterns: list[str]         # 命令执行错误的匹配模式列表
    md5_command: str                  # 设备端MD5校验命令模板


def _parse_check_list(items: list[dict]) -> list[CheckCommand]:
    """将YAML中的检查命令字典列表转换为CheckCommand对象列表"""
    return [CheckCommand(command=i["command"], key=i["key"]) for i in items]


def _parse_activate_list(items: list[dict]) -> list[ActivateCommand]:
    """将YAML中的激活/回退命令字典列表转换为ActivateCommand对象列表"""
    return [ActivateCommand(command=i["command"], description=i.get("description", "")) for i in items]


def load_profile(vendor: str, templates_dir: Path | None = None) -> VendorProfile:
    """
    根据厂商名称加载对应的YAML命令模板
    - vendor: Excel中填写的厂商名（支持别名）
    - templates_dir: 自定义模板目录，默认使用项目内置目录
    - 返回: 填充好的VendorProfile对象
    - 找不到模板文件时抛出FileNotFoundError
    """
    # 将厂商名统一为小写后通过别名映射
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
    """将命令模板中的占位符替换为实际值"""
    return template.replace("{patch_file}", patch_file).replace("{patch_id}", patch_id)
