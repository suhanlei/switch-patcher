"""
SSH连接管理模块
- 使用netmiko建立SSH连接，自动适配不同厂商的交互方式
- 认证失败直接抛异常（密码错误再试也没用）
- H3C设备使用40MB大接收缓冲区（display输出量大）
- 锐捷设备连接前等待1秒（限速保护，避免过快连接导致设备拒绝）
- 重试逻辑由device_worker统一控制，本模块不做重试
"""

import time
import logging

from netmiko import ConnectHandler, NetmikoAuthenticationException
from paramiko.ssh_exception import SSHException

from switch_patcher.vendor_profiles import VendorProfile
from switch_patcher.excel_io import DeviceInfo

logger = logging.getLogger(__name__)


def create_connection(
    device: DeviceInfo,
    profile: VendorProfile,
    username: str = "",
    password: str = "",
    ssh_port: int = 22,
    timeout: int = 30,
) -> ConnectHandler:
    """
    建立SSH连接到目标设备（单次尝试，不重试）
    - 使用厂商档案中的netmiko_type自动选择设备驱动
    - 认证失败直接抛异常
    - H3C设备自动设置大接收缓冲区（40MB）
    - 锐捷设备连接前自动等待（限速保护）
    - 返回: netmiko连接对象
    - 失败时抛出ConnectionError或对应异常
    """
    # 锐捷设备连接前等待：限速保护，避免过快连接导致设备拒绝
    if profile.connect_delay > 0:
        logger.debug(f"[{device.hostname}] Rate limit: sleeping {profile.connect_delay}s before connect")
        time.sleep(profile.connect_delay)

    params = {
        "device_type": profile.netmiko_type,
        "host": device.mgmt_ip,
        "username": username,
        "password": password,
        "port": ssh_port,
        "conn_timeout": timeout,
        "auth_timeout": 20,
        "banner_timeout": 20,
        "global_cmd_verify": False,
    }

    # H3C设备需要更大的接收缓冲区（display输出量大，40MB vs 默认400KB）
    if profile.recv_buffer_size != 409600:
        params["recv_buffer_size"] = profile.recv_buffer_size

    try:
        conn = ConnectHandler(**params)
        logger.info(f"[{device.hostname}] SSH connected to {device.mgmt_ip}")
        return conn
    except NetmikoAuthenticationException as e:
        logger.error(f"[{device.hostname}] Authentication failed: {e}")
        raise ConnectionError(f"Authentication failed: {device.mgmt_ip}") from e
    except Exception as e:
        raise ConnectionError(f"SSH connection failed: {device.mgmt_ip} - {e}") from e
