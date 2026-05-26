import re
import logging
from dataclasses import dataclass

from netmiko import ConnectHandler

from switch_patcher.vendor_profiles import VendorProfile, format_command

logger = logging.getLogger(__name__)

CPU_PATTERNS = {
    "H3C": [r"[Cc][Pp][Uu]\s*[Uu]sage[:\s]*(\d+)%?", r"(\d+)%"],
    "HUAWEI": [r"CPU\s+Usage\s*:\s*(\d+)%", r"(\d+)%"],
    "RUIJIE": [r"[Cc][Pp][Uu]\s+[Uu]tilization[:\s]*(\d+)%", r"(\d+)%"],
}

MEM_PATTERNS = {
    "H3C": [r"[Mm]emory\s*[Uu]sage[:\s]*(\d+)%?", r"(\d+)%"],
    "HUAWEI": [r"Memory\s+Usage\s*:\s*(\d+)%", r"(\d+)%"],
    "RUIJIE": [r"[Mm]emory\s+[Uu]tilization[:\s]*(\d+)%", r"(\d+)%"],
}


@dataclass
class HealthStatus:
    cpu_percent: float | None = None
    mem_percent: float | None = None
    raw_output: dict[str, str] | None = None
    patch_id: str | None = None
    warnings: list[str] | None = None

    def __post_init__(self):
        if self.raw_output is None:
            self.raw_output = {}
        if self.warnings is None:
            self.warnings = []


def _parse_percent(output: str, patterns: list[str]) -> float | None:
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
    result = HealthStatus()
    vendor = profile.vendor.upper()
    # normalize H3C variants
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
    try:
        output = conn.send_command(
            format_command(profile.pre_check[0].command),
            read_timeout=30,
        )
        return expected_patch_file.split("/")[-1].split(".")[0] in output or expected_patch_file in output
    except Exception as e:
        logger.warning(f"Patch verification failed: {e}")
        return False
