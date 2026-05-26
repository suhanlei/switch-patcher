"""
设备健康检查模块
- 执行厂商特定的display/show命令获取补丁状态
- 补丁版本提取：从display patch information中提取当前补丁名
- H3C特有错误模式检测：补丁已激活、补丁不兼容
- SCP/SFTP状态检查：确认设备是否已开启文件传输服务
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional, Dict, List

from netmiko import ConnectHandler

from switch_patcher.vendor_profiles import VendorProfile, format_command, ScpCheckCommand

logger = logging.getLogger(__name__)

# H3C特有错误模式：补丁已激活或补丁不兼容（从display patch information输出检测）
H3C_PATCH_ERROR_PATTERNS = [
    r"cannot be activated again",    # 补丁已激活，不能重复激活
    r"not compliant",                # 补丁与设备不兼容
    r"does not exist",               # 补丁文件不存在
]


@dataclass
class HealthStatus:
    """健康检查结果"""
    raw_output: Optional[Dict[str, str]] = None  # 各命令的原始输出（供人工复查）
    patch_id: Optional[str] = None            # 当前补丁版本号
    warnings: Optional[List[str]] = None      # 解析过程中的警告信息
    patch_errors: Optional[List[str]] = None  # H3C补丁状态错误信息

    def __post_init__(self):
        if self.raw_output is None:
            self.raw_output = {}
        if self.warnings is None:
            self.warnings = []
        if self.patch_errors is None:
            self.patch_errors = []


def run_health_checks(
    conn: ConnectHandler,
    profile: VendorProfile,
) -> HealthStatus:
    """
    执行健康检查命令并解析结果
    - 依次执行pre_check中定义的命令
    - 解析补丁版本
    - H3C设备额外检测补丁状态错误模式
    """
    result = HealthStatus()
    vendor = profile.vendor.upper()
    vendor_key = "HUAWEI" if vendor.startswith("HUAWEI") else ("RUIJIE" if vendor.startswith("RUIJIE") else "H3C")

    for cmd in profile.pre_check:
        formatted = format_command(cmd.command)
        try:
            output = conn.send_command(formatted, read_timeout=30)
            result.raw_output[cmd.key] = output

            if cmd.key == "patch_info":
                m = re.search(profile.patch_id_pattern, output)
                if m:
                    result.patch_id = m.group(1).strip()
                # H3C设备额外检测补丁状态错误模式
                if vendor_key == "H3C":
                    for err_pat in H3C_PATCH_ERROR_PATTERNS:
                        err_match = re.search(err_pat, output, re.IGNORECASE)
                        if err_match:
                            result.patch_errors.append(err_match.group(0))
                            logger.warning(f"H3C patch status error detected: {err_match.group(0)}")
        except Exception as e:
            logger.warning(f"Health check command '{cmd.key}' failed: {e}")
            result.warnings.append(f"{cmd.key}: {e}")

    return result


def verify_patch_applied(
    conn: ConnectHandler,
    profile: VendorProfile,
    expected_patch_file: str,
) -> bool:
    """
    补丁后检查：验证补丁是否已成功激活
    - 执行display patch information命令
    - 在输出中查找目标补丁文件名或编号
    """
    try:
        output = conn.send_command(
            format_command(profile.pre_check[0].command),
            read_timeout=30,
        )
        return expected_patch_file.split("/")[-1].split(".")[0] in output or expected_patch_file in output
    except Exception as e:
        logger.warning(f"Patch verification failed: {e}")
        return False


def check_scp_sftp(
    conn: ConnectHandler,
    profile: VendorProfile,
) -> str:
    """
    检查设备SCP/SFTP服务状态
    - 执行YAML模板中check_scp_commands定义的命令
    - 从输出中检测是否包含SCP/SFTP使能关键字
    - 返回: 'scp' / 'sftp' / 'scp_sftp' / 'none'
    """
    has_scp = False
    has_sftp = False

    for cmd in profile.check_scp_commands:
        try:
            output = conn.send_command(cmd.command, read_timeout=15)

            if cmd.key == "scp":
                if re.search(r"scp\s+\S*\s*\S*\s*enable", output, re.IGNORECASE):
                    has_scp = True
                elif re.search(r"scp server enable", output, re.IGNORECASE):
                    has_scp = True
                elif re.search(r"ip scp server enable", output, re.IGNORECASE):
                    has_scp = True

            elif cmd.key == "sftp":
                if re.search(r"sftp\s+\S*\s*\S*\s*enable", output, re.IGNORECASE):
                    has_sftp = True
                elif re.search(r"sftp server enable", output, re.IGNORECASE):
                    has_sftp = True

        except Exception as e:
            logger.warning(f"SCP check command '{cmd.key}' failed: {e}")

    if has_scp and has_sftp:
        return "scp_sftp"
    elif has_scp:
        return "scp"
    elif has_sftp:
        return "sftp"
    return "none"
