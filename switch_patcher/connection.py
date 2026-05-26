"""
SSH连接管理模块
- 使用netmiko建立SSH连接，自动适配不同厂商的交互方式
- 支持连接超时重试（默认2次，间隔2秒）
- 认证失败不重试（密码错误再试也没用）
- H3C设备使用40MB大接收缓冲区（display输出量大）
- 锐捷设备连接前等待1秒（限速保护，避免过快连接导致设备拒绝）
"""

import time
import logging

from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException
from paramiko.ssh_exception import SSHException

from switch_patcher.vendor_profiles import VendorProfile
from switch_patcher.excel_io import DeviceInfo

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS = 2     # 连接重试次数
RETRY_DELAY = 2        # 重试间隔（秒）


def create_connection(
    device: DeviceInfo,
    profile: VendorProfile,
    username: str = "",
    password: str = "",
    ssh_port: int = 22,
    timeout: int = 30,
    retries: int = RETRY_ATTEMPTS,
) -> ConnectHandler:
    """
    建立SSH连接到目标设备
    - 使用厂商档案中的netmiko_type自动选择设备驱动
    - 超时/网络异常自动重试，认证失败直接报错
    - H3C设备自动设置大接收缓冲区（40MB）
    - 锐捷设备连接前自动等待1秒（限速保护）
    - 返回: netmiko连接对象
    - 失败时抛出ConnectionError
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
        "conn_timeout": timeout,        # 连接超时
        "auth_timeout": 20,              # 认证超时
        "banner_timeout": 20,            # Banner超时
        "global_cmd_verify": False,      # 关闭全局命令确认，避免交互阻塞
    }

    # H3C设备需要更大的接收缓冲区（display输出量大，40MB vs 默认400KB）
    if profile.recv_buffer_size != 409600:
        params["recv_buffer_size"] = profile.recv_buffer_size

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            conn = ConnectHandler(**params)
            logger.info(f"SSH connected to {device.mgmt_ip} (attempt {attempt})")
            return conn
        except NetmikoAuthenticationException as e:
            # 认证失败，无需重试
            logger.error(f"Authentication failed for {device.mgmt_ip}: {e}")
            raise ConnectionError(f"Authentication failed: {device.mgmt_ip}") from e
        except (NetmikoTimeoutException, SSHException, OSError) as e:
            # 超时或网络异常，记录后重试
            last_err = e
            logger.warning(f"SSH connection attempt {attempt}/{retries} failed for {device.mgmt_ip}: {e}")
            if attempt < retries:
                time.sleep(RETRY_DELAY)

    raise ConnectionError(f"SSH connection failed after {retries} attempts: {device.mgmt_ip} - {last_err}")
