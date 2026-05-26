"""
厂商命令模板加载模块
- 从YAML文件中读取各厂商（H3C/华为/锐捷）的补丁操作命令模板
- 提供命令占位符替换功能（{patch_file}、{patch_id}）
- 支持厂商别名映射，兼容不同写法
- 支持交互式命令（Y/N确认）、进度等待（100%完成）、SCP/SFTP使能等高级特性
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
    """
    补丁激活/回退命令
    - command: 命令模板（可包含{patch_file}、{patch_id}占位符）
    - description: 命令中文描述
    - expect_pattern: 交互式提示匹配模式（如"[Yy]/[Nn]"），匹配到后自动回复auto_reply
    - auto_reply: 交互式自动回复内容（如"Y"）
    - wait_progress: 是否需要等待进度100%完成（锐捷特有）
    - progress_pattern: 进度百分比匹配正则（如"\\d+%"）
    - complete_pattern: 完成标志匹配正则（如"100%"）
    """
    command: str
    description: str = ""
    expect_pattern: str = ""
    auto_reply: str = ""
    wait_progress: bool = False
    progress_pattern: str = ""
    complete_pattern: str = ""


@dataclass
class ScpEnableCommand:
    """SCP/SFTP使能命令，用于在文件传输前确保设备已启用相关服务"""
    command: str
    description: str = ""


@dataclass
class VendorProfile:
    """
    厂商配置档案，包含该厂商补丁操作所需的全部命令和信息
    - recv_buffer_size: SSH接收缓冲区大小（H3C需要40MB，其他厂商400KB）
    - connect_delay: 连接前等待秒数（锐捷需要1秒限速保护）
    - scp_enable_commands: SCP/SFTP使能命令列表（文件传输前置条件）
    - verify_method: 文件校验方式（md5=哈希精确校验, size=文件大小比对）
    """
    vendor: str                              # 厂商标准名称
    netmiko_type: str                        # netmiko设备类型标识
    remote_dir: str                          # 设备端补丁存放目录
    recv_buffer_size: int = 409600           # SSH接收缓冲区字节数
    connect_delay: float = 0                  # 连接前等待秒数
    pre_check: list[CheckCommand] = field(default_factory=list)       # 补丁前健康检查命令列表
    activate: list[ActivateCommand] = field(default_factory=list)     # 补丁激活命令列表
    post_check: list[CheckCommand] = field(default_factory=list)       # 补丁后健康检查命令列表
    rollback: list[ActivateCommand] = field(default_factory=list)      # 回退命令列表
    scp_enable_commands: list[ScpEnableCommand] = field(default_factory=list)  # SCP/SFTP使能命令列表
    save: str = ""                           # 保存配置的命令
    patch_id_pattern: str = ""               # 从输出中提取补丁版本号的正则表达式
    error_patterns: list[str] = field(default_factory=list)  # 命令执行错误的匹配模式列表
    md5_command: str = ""                    # 设备端MD5校验命令模板
    verify_method: str = "md5"               # 文件校验方式：md5 或 size


def _parse_check_list(items: list[dict]) -> list[CheckCommand]:
    """将YAML中的检查命令字典列表转换为CheckCommand对象列表"""
    return [CheckCommand(command=i["command"], key=i["key"]) for i in items]


def _parse_activate_list(items: list[dict]) -> list[ActivateCommand]:
    """将YAML中的激活/回退命令字典列表转换为ActivateCommand对象列表"""
    result = []
    for i in items:
        result.append(ActivateCommand(
            command=i["command"],
            description=i.get("description", ""),
            expect_pattern=i.get("expect_pattern", ""),
            auto_reply=i.get("auto_reply", ""),
            wait_progress=i.get("wait_progress", False),
            progress_pattern=i.get("progress_pattern", ""),
            complete_pattern=i.get("complete_pattern", ""),
        ))
    return result


def _parse_scp_enable_list(items: list[dict] | None) -> list[ScpEnableCommand]:
    """将YAML中的SCP使能命令字典列表转换为ScpEnableCommand对象列表"""
    if not items:
        return []
    return [ScpEnableCommand(command=i["command"], description=i.get("description", "")) for i in items]


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
        recv_buffer_size=data.get("recv_buffer_size", 409600),
        connect_delay=data.get("connect_delay", 0),
        pre_check=_parse_check_list(data.get("pre_check", [])),
        activate=_parse_activate_list(data.get("activate", [])),
        post_check=_parse_check_list(data.get("post_check", [])),
        rollback=_parse_activate_list(data.get("rollback", [])),
        scp_enable_commands=_parse_scp_enable_list(data.get("scp_enable_commands")),
        save=data.get("save", ""),
        patch_id_pattern=data.get("patch_id_pattern", ""),
        error_patterns=data.get("error_patterns", []),
        md5_command=data.get("md5_command", ""),
        verify_method=data.get("verify_method", "md5"),
    )


def format_command(template: str, patch_file: str = "", patch_id: str = "") -> str:
    """将命令模板中的占位符替换为实际值"""
    return template.replace("{patch_file}", patch_file).replace("{patch_id}", patch_id)
