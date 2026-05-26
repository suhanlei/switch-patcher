import os
import logging
import socket
import struct
from pathlib import Path

import paramiko

from switch_patcher.vendor_profiles import VendorProfile, format_command
from switch_patcher.excel_io import DeviceInfo

logger = logging.getLogger(__name__)


def check_connectivity(mgmt_ip: str, port: int = 22, timeout: int = 5) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((mgmt_ip, port))
        sock.close()
        return result == 0
    except OSError:
        return False


def sftp_upload(
    device: DeviceInfo,
    profile: VendorProfile,
    local_file: str,
    remote_file: str,
    username: str = "",
    password: str = "",
    timeout: int = 120,
) -> bool:
    transport = None
    sftp = None
    try:
        transport = paramiko.Transport((device.mgmt_ip, device.ssh_port if hasattr(device, 'ssh_port') and device.ssh_port else 22))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)

        local_size = os.path.getsize(local_file)
        logger.info(f"Uploading {local_file} ({local_size} bytes) to {device.mgmt_ip}:{remote_file}")

        sftp.put(local_file, remote_file)

        remote_size = sftp.stat(remote_file).st_size
        if remote_size != local_size:
            logger.error(f"Size mismatch: local={local_size}, remote={remote_size}")
            return False

        logger.info(f"SFTP upload complete for {device.hostname}")
        return True

    except Exception as e:
        logger.error(f"SFTP upload failed for {device.hostname}: {e}")
        return False
    finally:
        if sftp:
            sftp.close()
        if transport:
            transport.close()


def verify_file_on_device(
    conn,
    profile: VendorProfile,
    patch_file: str,
    expected_md5: str = "",
) -> tuple[bool, str]:
    md5_cmd = format_command(profile.md5_command, patch_file=patch_file)
    try:
        output = conn.send_command(md5_cmd, read_timeout=120)

        if expected_md5:
            import re
            md5_match = re.search(r"([a-fA-F0-9]{32})", output)
            if md5_match:
                device_md5 = md5_match.group(1).lower()
                if device_md5 == expected_md5.lower():
                    logger.info(f"MD5 verified for {patch_file}")
                    return True, device_md5
                else:
                    logger.error(f"MD5 mismatch: local={expected_md5}, device={device_md5}")
                    return False, device_md5
            # If MD5 command doesn't output a hash, fall through to size check

        # Fallback: check file exists via dir command
        size_match = re.search(r"(\d+)\s+" + re.escape(patch_file.split("/")[-1]), output)
        if size_match:
            return True, size_match.group(1)

        return False, output[:200]

    except Exception as e:
        logger.error(f"File verification failed: {e}")
        return False, str(e)
