"""
文件传输模块
- TCP连通性检查：SSH前先探测端口，不可达直接返回False
- SFTP上传：通过paramiko SFTP通道将补丁文件推送到设备flash:/
- 设备端文件校验：
  - H3C/锐捷：执行MD5命令，比对32位哈希值精确校验
  - 华为：执行dir命令查看文件大小，间接验证完整性（华为不支持md5sum）
"""

import os
import re
import logging
import socket
from pathlib import Path
from typing import Tuple

import paramiko

from switch_patcher.vendor_profiles import VendorProfile, format_command
from switch_patcher.excel_io import DeviceInfo

logger = logging.getLogger(__name__)


def _send_cmd(conn, command: str, read_timeout: int = 120, delay_factor: float = 1.0) -> str:
    """兼容H3C Comware设备的命令发送（优先send_command_timing）"""
    try:
        return conn.send_command_timing(command, read_timeout=read_timeout, delay_factor=delay_factor, max_loops=500)
    except Exception:
        try:
            return conn.send_command(command, read_timeout=read_timeout, delay_factor=delay_factor, max_loops=500)
        except Exception as e:
            logger.warning(f"Command '{command}' failed: {e}")
            return ""


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
) -> Tuple[bool, str]:
    """
    在设备端校验已上传的补丁文件
    - 根据厂商verify_method选择校验方式：
      - md5方式（H3C/锐捷）：执行md5sum/verify命令，提取32位hex与本地比对
      - size方式（华为）：执行dir命令，检查文件大小是否大于0
    - 返回: (是否验证通过, 设备端校验值或错误信息)
    """
    verify_method = getattr(profile, "verify_method", "md5")
    md5_cmd = format_command(profile.md5_command, patch_file=patch_file)

    try:
        output = _send_cmd(conn, md5_cmd, read_timeout=120)

        if verify_method == "size":
            # 华为文件大小校验：从dir命令输出中提取文件大小
            # dir输出格式如："2024/01/15 10:30:00  52428800  S9855-CMW910-SYSTEM-R9131HS02.bin"
            size_match = re.search(r"(\d+)\s+" + re.escape(patch_file.split("/")[-1]), output)
            if size_match and int(size_match.group(1)) > 0:
                logger.info(f"File size verified for {patch_file}: {size_match.group(1)} bytes")
                return True, size_match.group(1)
            # 备用模式：更宽松地匹配文件名和大小
            size_match = re.search(r"(\d{6,})\s+\S*\s*" + re.escape(patch_file.split("/")[-1]), output)
            if size_match and int(size_match.group(1)) > 0:
                logger.info(f"File size verified (loose match) for {patch_file}: {size_match.group(1)} bytes")
                return True, size_match.group(1)
            logger.error(f"File size verification failed for {patch_file}, output: {output[:200]}")
            return False, output[:200]

        # 默认MD5校验方式（H3C/锐捷）
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

        # 未提供预期MD5时，退化为检查文件是否存在于输出中
        if patch_file.split("/")[-1] in output:
            logger.info(f"File existence verified for {patch_file}")
            return True, "exists"

        return False, output[:200]

    except Exception as e:
        logger.error(f"File verification failed: {e}")
        return False, str(e)
