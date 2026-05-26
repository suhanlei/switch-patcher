"""
单设备补丁执行流程模块
- 包含完整的6阶段流程：本地校验 → 预检查 → 文件传输 → 激活 → 后检查 → 生成回退
- 每个阶段独立建立/断开SSH连接，避免长连接超时
- 每个阶段完成后立即回写Excel，支持断点续跑
- SSH登录失败自动3次重试，间隔3秒
- 已成功的设备自动跳过，只重试失败设备
"""

import re
import logging
import time
from datetime import datetime
from pathlib import Path

from switch_patcher.vendor_profiles import VendorProfile, format_command
from switch_patcher.connection import create_connection
from switch_patcher.file_transfer import check_connectivity, sftp_upload, verify_file_on_device
from switch_patcher.health_check import run_health_checks, check_thresholds, verify_patch_applied
from switch_patcher.excel_io import DeviceInfo, DeviceResult, write_cell, calc_md5

logger = logging.getLogger(__name__)


def execute_device(
    device: DeviceInfo,
    profile: VendorProfile,
    excel_path: str,
    patches_dir: str,
    run_id: str,
    username: str = "",
    password: str = "",
    ssh_port: int = 22,
    timeout: int = 30,
    dry_run: bool = False,
    save_after_apply: bool = False,
    cpu_threshold: float = 90.0,
    mem_threshold: float = 90.0,
) -> DeviceResult:
    """
    执行单台设备的完整补丁流程
    - device: 设备信息对象
    - profile: 厂商命令模板
    - excel_path: Excel文件路径（用于回写状态）
    - patches_dir: 本地补丁文件目录
    - run_id: 本次执行批次ID（用于日志和回退文件命名）
    - dry_run: 预检查模式，只做健康检查不实际操作
    - save_after_apply: 激活后是否自动保存配置
    - 返回: DeviceResult对象
    """
    result = DeviceResult(
        hostname=device.hostname,
        mgmt_ip=device.mgmt_ip,
        vendor=device.vendor,
    )
    start_time = datetime.now()

    # ========== 断点续跑：跳过已成功的设备 ==========
    if device.update_result == "SUCCESS":
        logger.info(f"[{device.hostname}] Already patched, skipping")
        result.status = "skipped"
        result.patch_applied = True
        result.error_message = "Already completed in previous run"
        result.start_time = start_time
        result.end_time = datetime.now()
        return result

    # ========== 阶段0: 本地校验 ==========
    # 检查补丁文件是否存在，计算MD5
    local_patch_path = Path(patches_dir) / device.patch_file
    if not local_patch_path.exists():
        logger.error(f"[{device.hostname}] Patch file not found: {local_patch_path}")
        result.status = "failed"
        result.error_message = f"Local patch file not found: {device.patch_file}"
        write_cell(excel_path, device.row_index, "update_result", "FAIL-NOFILE")
        result.start_time = start_time
        result.end_time = datetime.now()
        return result

    # 计算本地文件MD5并回写Excel
    md5_local = calc_md5(str(local_patch_path))
    write_cell(excel_path, device.row_index, "md5_base", md5_local)
    logger.info(f"[{device.hostname}] Local MD5: {md5_local}")

    # ========== 连通性前置检查 ==========
    # SSH前先TCP探测，不可达直接标记，避免等待认证超时浪费时间
    if not check_connectivity(device.mgmt_ip, ssh_port):
        logger.error(f"[{device.hostname}] Device unreachable: {device.mgmt_ip}:{ssh_port}")
        result.status = "failed"
        result.error_message = f"Device unreachable: {device.mgmt_ip}:{ssh_port}"
        write_cell(excel_path, device.row_index, "login_mode", "UNREACHABLE")
        write_cell(excel_path, device.row_index, "update_result", "FAIL-UNREACHABLE")
        result.start_time = start_time
        result.end_time = datetime.now()
        return result

    # ========== 阶段1: 预检查 (SSH登录 + 健康检查) ==========
    conn = None
    health_before = None
    login_ok = False

    # SSH登录最多重试3次，每次间隔3秒
    for attempt in range(1, 4):
        try:
            conn = create_connection(device, profile, username, password, ssh_port, timeout)
            login_ok = True
            write_cell(excel_path, device.row_index, "login_mode", "OK")
            logger.info(f"[{device.hostname}] SSH login OK (attempt {attempt})")
            break
        except ConnectionError as e:
            logger.warning(f"[{device.hostname}] SSH login attempt {attempt}/3 failed: {e}")
            if attempt == 3:
                write_cell(excel_path, device.row_index, "login_mode", "FAIL")
                write_cell(excel_path, device.row_index, "update_result", "FAIL-LOGIN")
                result.status = "failed"
                result.error_message = f"SSH login failed after 3 attempts: {e}"
                result.start_time = start_time
                result.end_time = datetime.now()
                return result
            time.sleep(3)

    try:
        # 执行健康检查命令，获取CPU/内存/补丁版本
        health_before = run_health_checks(conn, profile)
        result.cpu_before = health_before.cpu_percent
        result.mem_before = health_before.mem_percent
        result.patch_now = health_before.patch_id or ""

        # 回写当前补丁版本和目标补丁版本到Excel
        write_cell(excel_path, device.row_index, "patch_now", result.patch_now)
        write_cell(excel_path, device.row_index, "patch_new", device.patch_file)

        # 判断CPU/内存是否超过阈值
        ok, reason = check_thresholds(health_before, cpu_threshold, mem_threshold)
        if not ok:
            logger.warning(f"[{device.hostname}] Pre-check threshold: {reason}")
            write_cell(excel_path, device.row_index, "update_result", f"SKIP-{reason}")
            result.status = "skipped"
            result.error_message = reason
            result.start_time = start_time
            result.end_time = datetime.now()
            return result

        result.pre_check_ok = True
        logger.info(f"[{device.hostname}] Pre-check OK: CPU={health_before.cpu_percent}%, MEM={health_before.mem_percent}%")
    except Exception as e:
        logger.error(f"[{device.hostname}] Pre-check exception: {e}")
        write_cell(excel_path, device.row_index, "update_result", "FAIL-PRECHECK")
        result.status = "failed"
        result.error_message = str(e)
        result.start_time = start_time
        result.end_time = datetime.now()
        return result
    finally:
        if conn:
            conn.disconnect()
            conn = None

    # ========== 阶段2: 文件传输 ==========
    # 如果上次执行已上传成功，可跳过传输阶段
    if device.upload_success == "OK":
        logger.info(f"[{device.hostname}] File already uploaded, skipping transfer")
        result.transfer_ok = True
    else:
        # 构造远端路径并上传
        remote_path = f"{profile.remote_dir}{device.patch_file}"
        upload_ok = sftp_upload(
            device, profile,
            str(local_patch_path), remote_path,
            username, password, ssh_port,
        )
        if not upload_ok:
            write_cell(excel_path, device.row_index, "upload_success", "FAIL")
            write_cell(excel_path, device.row_index, "update_result", "FAIL-UPLOAD")
            result.status = "failed"
            result.error_message = "SFTP upload failed"
            result.start_time = start_time
            result.end_time = datetime.now()
            return result

        # 上传后重连设备，校验文件完整性
        for attempt in range(1, 4):
            try:
                conn = create_connection(device, profile, username, password, ssh_port, timeout)
                break
            except ConnectionError:
                if attempt == 3:
                    write_cell(excel_path, device.row_index, "upload_success", "FAIL-VERIFY")
                    result.status = "failed"
                    result.error_message = "Cannot reconnect for verification"
                    result.start_time = start_time
                    result.end_time = datetime.now()
                    return result
                time.sleep(3)

        try:
            # 在设备端执行MD5校验命令，比对本地和远端MD5
            verified, md5_val = verify_file_on_device(conn, profile, device.patch_file, md5_local)
            if verified:
                write_cell(excel_path, device.row_index, "upload_success", "OK")
                write_cell(excel_path, device.row_index, "md5_uploaded", md5_val)
                result.transfer_ok = True
            else:
                # MD5不匹配，文件可能损坏，不进入激活阶段
                write_cell(excel_path, device.row_index, "upload_success", "FAIL-MD5")
                write_cell(excel_path, device.row_index, "update_result", "FAIL-MD5")
                result.status = "failed"
                result.error_message = f"MD5 verification failed: {md5_val}"
                result.start_time = start_time
                result.end_time = datetime.now()
                return result
        except Exception as e:
            logger.error(f"[{device.hostname}] Verify exception: {e}")
            write_cell(excel_path, device.row_index, "upload_success", "FAIL-VERIFY")
            result.status = "failed"
            result.error_message = str(e)
            result.start_time = start_time
            result.end_time = datetime.now()
            return result
        finally:
            if conn:
                conn.disconnect()
                conn = None

    # ========== 预检查模式到此结束 ==========
    if dry_run:
        logger.info(f"[{device.hostname}] Dry-run complete, skipping activate")
        write_cell(excel_path, device.row_index, "update_result", "DRYRUN-OK")
        result.status = "skipped"
        result.start_time = start_time
        result.end_time = datetime.now()
        return result

    # ========== 阶段3: 激活补丁 ==========
    # 重新建立SSH连接
    for attempt in range(1, 4):
        try:
            conn = create_connection(device, profile, username, password, ssh_port, timeout)
            break
        except ConnectionError:
            if attempt == 3:
                write_cell(excel_path, device.row_index, "update_result", "FAIL-ACTIVATE-LOGIN")
                result.status = "failed"
                result.error_message = "Cannot reconnect for activation"
                result.start_time = start_time
                result.end_time = datetime.now()
                return result
            time.sleep(3)

    applied_cmds = []   # 记录成功执行的命令，用于生成回退文件
    try:
        # 进入配置模式（netmiko自动处理厂商差异：H3C system-view, Huawei system-view, Ruijie configure terminal）
        conn.config_mode()
        for cmd in profile.activate:
            # 替换命令模板中的占位符
            formatted = format_command(cmd.command, patch_file=device.patch_file, patch_id=device.patch_file)
            logger.info(f"[{device.hostname}] Executing: {formatted}")
            try:
                # 逐条发送命令，不自动退出配置模式，以便发送下一条
                output = conn.send_config_set([formatted], exit_config_mode=False, read_timeout=120)
                # 检查输出中是否包含错误模式
                has_error = any(re.search(pat, output, re.IGNORECASE) for pat in profile.error_patterns)
                if has_error:
                    logger.error(f"[{device.hostname}] Command error: {formatted} -> {output[:200]}")
                    result.commands_failed += 1
                else:
                    logger.info(f"[{device.hostname}] Command OK: {formatted}")
                    applied_cmds.append((cmd, formatted))
                    result.commands_applied += 1
            except Exception as e:
                # 单条命令异常不影响后续命令执行
                logger.error(f"[{device.hostname}] Command exception: {formatted} -> {e}")
                result.commands_failed += 1
            result.commands_total += 1

        conn.exit_config_mode()

        # 默认不保存配置，给人工验证留窗口期
        if save_after_apply:
            logger.info(f"[{device.hostname}] Saving configuration...")
            conn.save_config()
    except Exception as e:
        logger.error(f"[{device.hostname}] Activate phase exception: {e}")
    finally:
        if conn:
            conn.disconnect()
            conn = None

    # ========== 阶段4: 后检查 ==========
    # 重新连接，检查补丁后的设备状态
    for attempt in range(1, 4):
        try:
            conn = create_connection(device, profile, username, password, ssh_port, timeout)
            break
        except ConnectionError:
            if attempt == 3:
                break
            time.sleep(3)

    if conn:
        try:
            health_after = run_health_checks(conn, profile)
            result.cpu_after = health_after.cpu_percent
            result.mem_after = health_after.mem_percent
            result.post_check_ok = True
            # 验证补丁是否已激活
            result.patch_applied = verify_patch_applied(conn, profile, device.patch_file)
            logger.info(f"[{device.hostname}] Post-check: CPU={health_after.cpu_percent}%, MEM={health_after.mem_percent}%, Patch={result.patch_applied}")
        except Exception as e:
            logger.warning(f"[{device.hostname}] Post-check failed: {e}")
        finally:
            conn.disconnect()

    # ========== 阶段5: 生成回退文件 ==========
    # 只为成功执行的激活步骤生成对应的回退命令
    if applied_cmds:
        rollback_dir = Path(excel_path).parent / "rollback"
        rollback_dir.mkdir(exist_ok=True)
        rollback_path = rollback_dir / f"{device.hostname}_{run_id}.txt"

        lines = [
            f"# Rollback commands for: {device.hostname} ({device.mgmt_ip})",
            f"# Vendor: {device.vendor}",
            f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"# Patch file: {device.patch_file}",
            f"# Run ID: {run_id}",
            f"# WARNING: Review before executing. Apply in the order listed.",
            "",
        ]
        # 回退命令按反序排列（最后执行的先回退）
        for act_cmd, formatted in reversed(applied_cmds):
            rb_cmd = _find_rollback_cmd(profile, act_cmd.command, device.patch_file)
            lines.append(rb_cmd)

        rollback_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"[{device.hostname}] Rollback file: {rollback_path}")

    # ========== 最终状态判定 ==========
    if result.commands_applied > 0 and result.commands_failed == 0:
        result.status = "success"
        write_cell(excel_path, device.row_index, "update_result", "SUCCESS")
    elif result.commands_applied > 0:
        result.status = "partial"
        write_cell(excel_path, device.row_index, "update_result", "PARTIAL")
    else:
        result.status = "failed"
        write_cell(excel_path, device.row_index, "update_result", "FAIL-ACTIVATE")

    result.patch_new = device.patch_file
    result.start_time = start_time
    result.end_time = datetime.now()
    return result


def _find_rollback_cmd(profile: VendorProfile, activate_cmd: str, patch_file: str) -> str:
    """
    根据激活命令查找对应的回退命令
    - 按索引对应：activate[0] 对应 rollback[0]
    - 找不到对应时返回注释行
    """
    for i, act_cmd in enumerate(profile.activate):
        if act_cmd.command == activate_cmd:
            if i < len(profile.rollback):
                return format_command(profile.rollback[i].command, patch_file=patch_file, patch_id=patch_file)
    return f"# No rollback found for: {activate_cmd}"
