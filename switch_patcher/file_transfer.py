"""
文件传输模块
- TCP连通性检查：SSH前先探测端口，不可达直接标记UNREACHABLE
- SFTP上传：通过paramiko SFTP通道将补丁文件推送到设备flash:/
- 设备端文件校验：上传后执行MD5/文件大小命令比对完整性
"""

import os
import re
import logging
import socket
from pathlib import Path

import paramiko

from switch_patcher.vendor_profiles import VendorProfile, format_command
from switch_patcher.excel_io import DeviceInfo

logger = logging.getLogger(__name__)


def check_connectivity(mgmt_ip: str, port: int = 22, timeout: int = 5) -> bool:
    """
    TCP端口探测，检查设备管理口是否可达
    - 比SSH连接更轻量，避免在不可达时等待认证超时
    """
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
    ssh_port: int = 22,
    timeout: int = 120,
) -> bool:
    """
    通过SFTP上传补丁文件到设备
    - 使用独立的paramiko Transport通道（复用SSH认证信息）
    - 上传后比对本地和远端文件大小，大小不一致则判定失败
    - 返回: True=上传成功, False=失败
    """
    transport = None
    sftp = None
    try:
        # 建立SFTP传输通道
        transport = paramiko.Transport((device.mgmt_ip, ssh_port))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)

        local_size = os.path.getsize(local_file)
        logger.info(f"Uploading {local_file} ({local_size} bytes) to {device.mgmt_ip}:{remote_file}")

        # 执行上传
        sftp.put(local_file, remote_file)

        # 比对文件大小确认上传完整
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
        # 确保关闭SFTP和Transport通道
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
    """
    在设备端校验已上传的补丁文件
    - 优先使用MD5比对：执行设备端md5sum命令，提取32位hex与本地比对
    - MD5不可用时退化为文件大小检查：通过dir命令输出判断文件是否存在
    - 返回: (是否验证通过, 设备端MD5或文件大小或错误信息)
    """
    md5_cmd = format_command(profile.md5_command, patch_file=patch_file)
    try:
        output = conn.send_command(md5_cmd, read_timeout=120)

        if expected_md5:
            # 从输出中提取32位MD5哈希值
            md5_match = re.search(r"([a-fA-F0-9]{32})", output)
            if md5_match:
                device_md5 = md5_match.group(1).lower()
                if device_md5 == expected_md5.lower():
                    logger.info(f"MD5 verified for {patch_file}")
                    return True, device_md5
                else:
                    logger.error(f"MD5 mismatch: local={expected_md5}, device={device_md5}")
                    return False, device_md5
            # MD5命令未输出哈希值，退化为文件大小检查

        # 退化为检查文件是否存在（通过dir命令输出中的文件名和大小）
        size_match = re.search(r"(\d+)\s+" + re.escape(patch_file.split("/")[-1]), output)
        if size_match:
            return True, size_match.group(1)

        return False, output[:200]

    except Exception as e:
        logger.error(f"File verification failed: {e}")
        return False, str(e)
