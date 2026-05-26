import time
import logging

from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException
from paramiko.ssh_exception import SSHException

from switch_patcher.vendor_profiles import VendorProfile
from switch_patcher.excel_io import DeviceInfo

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS = 2
RETRY_DELAY = 2


def create_connection(
    device: DeviceInfo,
    profile: VendorProfile,
    username: str = "",
    password: str = "",
    ssh_port: int = 22,
    timeout: int = 30,
    retries: int = RETRY_ATTEMPTS,
) -> ConnectHandler:
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

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            conn = ConnectHandler(**params)
            logger.info(f"SSH connected to {device.mgmt_ip} (attempt {attempt})")
            return conn
        except NetmikoAuthenticationException as e:
            logger.error(f"Authentication failed for {device.mgmt_ip}: {e}")
            raise ConnectionError(f"Authentication failed: {device.mgmt_ip}") from e
        except (NetmikoTimeoutException, SSHException, OSError) as e:
            last_err = e
            logger.warning(f"SSH connection attempt {attempt}/{retries} failed for {device.mgmt_ip}: {e}")
            if attempt < retries:
                time.sleep(RETRY_DELAY)

    raise ConnectionError(f"SSH connection failed after {retries} attempts: {device.mgmt_ip} - {last_err}")
