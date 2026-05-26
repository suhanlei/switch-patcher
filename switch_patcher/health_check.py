"""
设备健康检查模块
- 执行厂商特定的display/show命令获取CPU、内存、补丁状态
- 通过正则表达式解析各厂商不同格式的输出
- 阈值判断：CPU或内存超过设定值则建议跳过
- 补丁版本提取：从display patch information中提取当前补丁名
"""

import re
import logging
from dataclasses import dataclass

from netmiko import ConnectHandler

from switch_patcher.vendor_profiles import VendorProfile, format_command

logger = logging.getLogger(__name__)

# 各厂商CPU使用率输出的正则匹配模式（按优先级排列，先匹配精确模式）
CPU_PATTERNS = {
    "H3C": [r"[Cc][Pp][Uu]\s*[Uu]sage[:\s]*(\d+)%?", r"(\d+)%"],
    "HUAWEI": [r"CPU\s+Usage\s*:\s*(\d+)%", r"(\d+)%"],
    "RUIJIE": [r"[Cc][Pp][Uu]\s+[Uu]tilization[:\s]*(\d+)%", r"(\d+)%"],
}

# 各厂商内存使用率输出的正则匹配模式
MEM_PATTERNS = {
    "H3C": [r"[Mm]emory\s*[Uu]sage[:\s]*(\d+)%?", r"(\d+)%"],
    "HUAWEI": [r"Memory\s+Usage\s*:\s*(\d+)%", r"(\d+)%"],
    "RUIJIE": [r"[Mm]emory\s+[Uu]tilization[:\s]*(\d+)%", r"(\d+)%"],
}


@dataclass
class HealthStatus:
    """健康检查结果"""
    cpu_percent: float | None = None      # CPU使用率百分比
    mem_percent: float | None = None      # 内存使用率百分比
    raw_output: dict[str, str] | None = None  # 各命令的原始输出（供人工复查）
    patch_id: str | None = None            # 当前补丁版本号
    warnings: list[str] | None = None      # 解析过程中的警告信息

    def __post_init__(self):
        if self.raw_output is None:
            self.raw_output = {}
        if self.warnings is None:
            self.warnings = []


def _parse_percent(output: str, patterns: list[str]) -> float | None:
    """使用正则列表按序尝试从输出中提取百分比值"""
    for pat in patterns:
        m = re.search(pat, output)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def run_health_checks(
    conn: ConnectHandler,
    profile: VendorProfile,
) -> HealthStatus:
    """
    执行全部健康检查命令并解析结果
    - 依次执行pre_check中定义的命令
    - 解析CPU、内存、补丁版本
    - 解析失败时记录警告但不中断流程
    """
    result = HealthStatus()
    vendor = profile.vendor.upper()
    # 将厂商名归一化到三大类：HUAWEI、RUIJIE、H3C
    vendor_key = "HUAWEI" if vendor.startswith("HUAWEI") else ("RUIJIE" if vendor.startswith("RUIJIE") else "H3C")

    for cmd in profile.pre_check:
        formatted = format_command(cmd.command)
        try:
            output = conn.send_command(formatted, read_timeout=30)
            result.raw_output[cmd.key] = output

            if cmd.key == "cpu":
                result.cpu_percent = _parse_percent(output, CPU_PATTERNS.get(vendor_key, []))
                if result.cpu_percent is None:
                    result.warnings.append("CPU parse failed")
            elif cmd.key == "memory":
                result.mem_percent = _parse_percent(output, MEM_PATTERNS.get(vendor_key, []))
                if result.mem_percent is None:
                    result.warnings.append("Memory parse failed")
            elif cmd.key == "patch_info":
                # 使用YAML中定义的正则从补丁信息中提取当前补丁名
                m = re.search(profile.patch_id_pattern, output)
                if m:
                    result.patch_id = m.group(1).strip()
        except Exception as e:
            logger.warning(f"Health check command '{cmd.key}' failed: {e}")
            result.warnings.append(f"{cmd.key}: {e}")

    return result


def check_thresholds(
    health: HealthStatus,
    cpu_threshold: float = 90.0,
    mem_threshold: float = 90.0,
) -> tuple[bool, str]:
    """
    判断设备健康状态是否满足打补丁条件
    - CPU或内存超过阈值 → 不通过
    - CPU和内存都解析失败 → 不通过（无法评估风险）
    - 返回: (是否通过, 原因描述)
    """
    if health.cpu_percent is not None and health.cpu_percent > cpu_threshold:
        return False, f"CPU {health.cpu_percent}% > threshold {cpu_threshold}%"
    if health.mem_percent is not None and health.mem_percent > mem_threshold:
        return False, f"Memory {health.mem_percent}% > threshold {mem_threshold}%"
    if health.cpu_percent is None and health.mem_percent is None:
        return False, "Both CPU and memory values unparseable"
    return True, ""


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
        # 检查补丁文件名（去掉路径和扩展名后）是否出现在输出中
        return expected_patch_file.split("/")[-1].split(".")[0] in output or expected_patch_file in output
    except Exception as e:
        logger.warning(f"Patch verification failed: {e}")
        return False
