"""
设备健康检查模块
- 执行厂商特定的display/show命令获取补丁状态
- 补丁版本提取：从display命令输出中提取当前补丁名
- 补丁已激活检测：判断目标补丁是否已在设备上激活
- SCP/SFTP状态检查：确认设备是否已开启文件传输服务
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional, Dict, List

from netmiko import ConnectHandler

from switch_patcher.vendor_profiles import VendorProfile, format_command, ScpCheckCommand

logger = logging.getLogger(__name__)


def _send_cmd(conn, command: str, read_timeout: int = 30, delay_factor: float = 1.0) -> str:
    """兼容H3C Comware设备的命令发送（优先send_command_timing）"""
    try:
        return conn.send_command_timing(command, read_timeout=read_timeout, delay_factor=delay_factor, max_loops=500)
    except Exception:
        try:
            return conn.send_command(command, read_timeout=read_timeout, delay_factor=delay_factor, max_loops=500)
        except Exception as e:
            logger.warning(f"Command '{command}' failed: {e}")
            return ""


@dataclass
class HealthStatus:
    """健康检查结果"""
    raw_output: Optional[Dict[str, str]] = None
    patch_id: Optional[str] = None
    warnings: Optional[List[str]] = None
    patch_already_active: bool = False

    def __post_init__(self):
        if self.raw_output is None:
            self.raw_output = {}
        if self.warnings is None:
            self.warnings = []


def run_health_checks(
    conn: ConnectHandler,
    profile: VendorProfile,
    expected_patch_file: str = "",
) -> HealthStatus:
    """
    执行健康检查命令并解析结果
    - 依次执行pre_check中定义的命令
    - 解析补丁版本
    - 检测目标补丁是否已在设备上激活
    """
    result = HealthStatus()

    for cmd in profile.pre_check:
        formatted = format_command(cmd.command)
        try:
            output = _send_cmd(conn, formatted, read_timeout=30)
            result.raw_output[cmd.key] = output

            if cmd.key == "patch_info":
                m = re.search(profile.patch_id_pattern, output)
                if m:
                    result.patch_id = m.group(1).strip()
                # 检测目标补丁是否已在设备上激活
                if expected_patch_file:
                    basename = expected_patch_file.split("/")[-1]
                    if basename.lower() in output.lower():
                        result.patch_already_active = True
                        logger.info(f"Patch already active: {basename}")
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
    - H3C: 检查 display install active 输出中是否包含补丁文件名
    - 华为: 检查 display patch-information 中 State 是否为 Running
    - 通用: 检查 display patch information 输出中是否包含补丁文件名
    """
    vendor = profile.vendor.upper()
    basename = expected_patch_file.split("/")[-1].split(".")[0]
    filename = expected_patch_file.split("/")[-1]

    try:
        if vendor.startswith("H3C") or vendor.startswith("NEW_H3C") or vendor.startswith("HP"):
            # H3C: 先看 display install active
            output = _send_cmd(conn, "display install active", read_timeout=30)
            logger.info(f"Post-check [display install active]: {output[:300] if output else '(empty)'}")
            if filename in output or basename in output:
                return True
            # 兜底: display patch information
            output2 = _send_cmd(conn, "display patch information", read_timeout=30)
            logger.info(f"Post-check [display patch information]: {output2[:300] if output2 else '(empty)'}")
            return filename in output2 or basename in output2

        if vendor.startswith("HUAWEI"):
            # 华为: display patch-information 查看 State 是否为 Running
            output = _send_cmd(conn, "display patch-information", read_timeout=30)
            logger.info(f"Post-check [display patch-information]: {output[:300] if output else '(empty)'}")
            if "Running" in output and (filename in output or basename in output):
                return True
            return basename in output

        # 其他厂商: 通用检查
        output = _send_cmd(
            conn,
            format_command(profile.pre_check[0].command),
            read_timeout=30,
        )
        return basename in output or filename in output
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
            output = _send_cmd(conn, cmd.command, read_timeout=15)

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
