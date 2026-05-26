"""
单设备补丁执行步骤模块
- 每个步骤是独立函数：SSH连接→执行→回写Excel→断开连接
- 步骤函数由batch_engine按编排顺序调用
- 每步基于Excel字段判断是否需要执行
- 支持交互式Y/N确认自动回复（H3C install activate、华为 patch load）
- 支持锐捷3步流程（upgrade→active→running），每步等待进度100%完成
- 死循环保护：所有while循环最多60次迭代后强制退出
- 保存配置时自动处理Y/N确认交互
"""

import re
import logging
import time
from datetime import datetime
from pathlib import Path

from switch_patcher.vendor_profiles import VendorProfile, format_command
from switch_patcher.connection import create_connection
from switch_patcher.file_transfer import check_connectivity, sftp_upload, verify_file_on_device
from switch_patcher.health_check import run_health_checks, check_thresholds, verify_patch_applied, check_scp_sftp
from switch_patcher.excel_io import DeviceInfo, DeviceResult, write_cell, calc_md5

logger = logging.getLogger(__name__)

MAX_LOOP_ITERATIONS = 60


def _write(device: DeviceInfo, col_name: str, value: str):
    """回写Excel的简写，自动带上excel_path和sheet_name"""
    write_cell(device.excel_path, device.row_index, col_name, value,
               sheet_name=device.sheet_name)


def _connect_with_retry(device, profile, username, password, ssh_port, timeout, max_retries=3):
    """SSH登录重试（最多3次，间隔3秒），返回连接对象或None"""
    for attempt in range(1, max_retries + 1):
        try:
            conn = create_connection(device, profile, username, password, ssh_port, timeout)
            logger.info(f"[{device.hostname}] SSH login OK (attempt {attempt})")
            return conn
        except ConnectionError as e:
            logger.warning(f"[{device.hostname}] SSH login attempt {attempt}/{max_retries} failed: {e}")
            if attempt == max_retries:
                _write(device, "scp_status", "login_fail")
                _write(device, "update_result", "FAIL-LOGIN")
                return None
            time.sleep(3)


def step_check_scp(device: DeviceInfo, profile: VendorProfile, **kwargs) -> str:
    """
    步骤1: 检查设备SCP/SFTP服务状态
    - 登录设备，执行check_scp_commands中的命令
    - 将结果写入Excel scp_status列
    - 返回: 'scp'/'sftp'/'scp_sftp'/'none'/'unreachable'/'login_fail'
    """
    username = kwargs.get("username", "")
    password = kwargs.get("password", "")
    ssh_port = kwargs.get("ssh_port", 22)
    timeout = kwargs.get("timeout", 30)

    # 先TCP探测
    if not check_connectivity(device.mgmt_ip, ssh_port):
        _write(device, "scp_status", "unreachable")
        _write(device, "update_result", "FAIL-UNREACHABLE")
        return "unreachable"

    conn = _connect_with_retry(device, profile, username, password, ssh_port, timeout)
    if not conn:
        return "login_fail"

    try:
        status = check_scp_sftp(conn, profile)
        _write(device, "scp_status", status)
        logger.info(f"[{device.hostname}] SCP/SFTP status: {status}")
        return status
    except Exception as e:
        logger.error(f"[{device.hostname}] Check SCP failed: {e}")
        _write(device, "scp_status", "none")
        return "none"
    finally:
        conn.disconnect()


def step_enable_scp(device: DeviceInfo, profile: VendorProfile, **kwargs) -> bool:
    """
    步骤2: 开启设备SCP/SFTP服务
    - 仅对scp_status=none的设备执行
    - 执行scp_enable_commands中的命令
    - 执行后保存配置（处理Y/N交互）
    - 返回: True=成功, False=失败
    """
    username = kwargs.get("username", "")
    password = kwargs.get("password", "")
    ssh_port = kwargs.get("ssh_port", 22)
    timeout = kwargs.get("timeout", 30)

    conn = _connect_with_retry(device, profile, username, password, ssh_port, timeout)
    if not conn:
        return False

    try:
        for scp_cmd in profile.scp_enable_commands:
            formatted = format_command(scp_cmd.command)
            logger.info(f"[{device.hostname}] Enabling: {formatted}")
            conn.send_command(formatted, read_timeout=30)

        _save_config(conn, profile)
        logger.info(f"[{device.hostname}] SCP/SFTP enabled and saved")
        return True
    except Exception as e:
        logger.error(f"[{device.hostname}] Enable SCP failed: {e}")
        return False
    finally:
        conn.disconnect()


def step_upload(device: DeviceInfo, profile: VendorProfile, **kwargs) -> bool:
    """
    步骤3: 上传补丁文件
    - 仅对upload_success≠OK的设备执行
    - 先检查本地补丁文件、计算MD5
    - TCP连通性检查
    - SFTP上传 + 设备端文件校验
    - 返回: True=上传成功, False=失败
    """
    username = kwargs.get("username", "")
    password = kwargs.get("password", "")
    ssh_port = kwargs.get("ssh_port", 22)
    timeout = kwargs.get("timeout", 30)
    patches_dir = kwargs.get("patches_dir", "patches")

    # 本地文件检查
    local_patch_path = Path(patches_dir) / device.patch_file
    if not local_patch_path.exists():
        logger.error(f"[{device.hostname}] Patch file not found: {local_patch_path}")
        _write(device, "update_result", "FAIL-NOFILE")
        return False

    md5_local = calc_md5(str(local_patch_path))
    _write(device, "md5_base", md5_local)

    # 连通性检查
    if not check_connectivity(device.mgmt_ip, ssh_port):
        logger.error(f"[{device.hostname}] Device unreachable")
        _write(device, "scp_status", "unreachable")
        _write(device, "update_result", "FAIL-UNREACHABLE")
        return False

    # SFTP上传
    remote_path = f"{profile.remote_dir}{device.patch_file}"
    upload_ok = sftp_upload(device, profile, str(local_patch_path), remote_path, username, password, ssh_port)
    if not upload_ok:
        _write(device, "upload_success", "FAIL")
        _write(device, "update_result", "FAIL-UPLOAD")
        return False

    # 上传后重连校验文件完整性
    conn = _connect_with_retry(device, profile, username, password, ssh_port, timeout)
    if not conn:
        _write(device, "upload_success", "FAIL-VERIFY")
        return False

    try:
        verified, verify_val = verify_file_on_device(conn, profile, device.patch_file, md5_local)
        if verified:
            _write(device, "upload_success", "OK")
            _write(device, "md5_uploaded", verify_val)
            logger.info(f"[{device.hostname}] Upload verified OK")
            return True
        else:
            _write(device, "upload_success", "FAIL-MD5")
            _write(device, "update_result", "FAIL-MD5")
            return False
    except Exception as e:
        logger.error(f"[{device.hostname}] Verify exception: {e}")
        _write(device, "upload_success", "FAIL-VERIFY")
        return False
    finally:
        conn.disconnect()


def step_activate(device: DeviceInfo, profile: VendorProfile, **kwargs) -> DeviceResult:
    """
    步骤4: 激活补丁
    - 仅对upload_success=OK且update_result≠SUCCESS的设备执行
    - 预检查：健康检查 + 阈值判断
    - 执行activate命令，处理Y/N交互和进度等待
    - 返回: DeviceResult对象
    """
    username = kwargs.get("username", "")
    password = kwargs.get("password", "")
    ssh_port = kwargs.get("ssh_port", 22)
    timeout = kwargs.get("timeout", 30)
    dry_run = kwargs.get("dry_run", False)
    save_after_apply = kwargs.get("save", False)
    cpu_threshold = kwargs.get("cpu_threshold", 90.0)
    mem_threshold = kwargs.get("mem_threshold", 90.0)

    result = DeviceResult(
        hostname=device.hostname,
        mgmt_ip=device.mgmt_ip,
        vendor=device.vendor,
    )
    start_time = datetime.now()

    # 连接设备做预检查
    conn = _connect_with_retry(device, profile, username, password, ssh_port, timeout)
    if not conn:
        result.status = "failed"
        result.error_message = "SSH login failed for activation"
        result.start_time = start_time
        result.end_time = datetime.now()
        return result

    try:
        health_before = run_health_checks(conn, profile)
        result.cpu_before = health_before.cpu_percent
        result.mem_before = health_before.mem_percent
        result.patch_now = health_before.patch_id or ""
        _write(device, "patch_now", result.patch_now)
        _write(device, "patch_new", device.patch_file)

        ok, reason = check_thresholds(health_before, cpu_threshold, mem_threshold)
        if not ok:
            logger.warning(f"[{device.hostname}] Pre-check threshold: {reason}")
            _write(device, "update_result", f"SKIP-{reason}")
            result.status = "skipped"
            result.error_message = reason
            result.start_time = start_time
            result.end_time = datetime.now()
            return result

        result.pre_check_ok = True
        logger.info(f"[{device.hostname}] Pre-check OK: CPU={health_before.cpu_percent}%, MEM={health_before.mem_percent}%")
    except Exception as e:
        logger.error(f"[{device.hostname}] Pre-check exception: {e}")
        _write(device, "update_result", "FAIL-PRECHECK")
        result.status = "failed"
        result.error_message = str(e)
        result.start_time = start_time
        result.end_time = datetime.now()
        return result
    finally:
        conn.disconnect()

    # 预检查模式到此结束
    if dry_run:
        logger.info(f"[{device.hostname}] Dry-run complete")
        _write(device, "update_result", "DRYRUN-OK")
        result.status = "skipped"
        result.start_time = start_time
        result.end_time = datetime.now()
        return result

    # 重新连接激活补丁
    conn = _connect_with_retry(device, profile, username, password, ssh_port, timeout)
    if not conn:
        _write(device, "update_result", "FAIL-ACTIVATE-LOGIN")
        result.status = "failed"
        result.error_message = "Cannot reconnect for activation"
        result.start_time = start_time
        result.end_time = datetime.now()
        return result

    applied_cmds = []
    try:
        conn.config_mode()
        for cmd in profile.activate:
            formatted = format_command(cmd.command, patch_file=device.patch_file, patch_id=device.patch_file)
            logger.info(f"[{device.hostname}] Executing: {formatted}")
            try:
                if cmd.wait_progress:
                    output = _send_command_wait_progress(conn, formatted, cmd)
                elif cmd.expect_pattern:
                    output = _send_command_interactive(conn, formatted, cmd)
                else:
                    output = conn.send_config_set([formatted], exit_config_mode=False, read_timeout=120)

                has_error = any(re.search(pat, output, re.IGNORECASE) for pat in profile.error_patterns)
                if has_error:
                    logger.error(f"[{device.hostname}] Command error: {formatted} -> {output[:200]}")
                    result.commands_failed += 1
                else:
                    logger.info(f"[{device.hostname}] Command OK: {formatted}")
                    applied_cmds.append((cmd, formatted))
                    result.commands_applied += 1
            except Exception as e:
                logger.error(f"[{device.hostname}] Command exception: {formatted} -> {e}")
                result.commands_failed += 1
            result.commands_total += 1

        conn.exit_config_mode()

        if save_after_apply:
            logger.info(f"[{device.hostname}] Saving configuration...")
            _save_config(conn, profile)
    except Exception as e:
        logger.error(f"[{device.hostname}] Activate phase exception: {e}")
    finally:
        if conn:
            conn.disconnect()

    # 后检查
    conn = _connect_with_retry(device, profile, username, password, ssh_port, timeout)
    if conn:
        try:
            health_after = run_health_checks(conn, profile)
            result.cpu_after = health_after.cpu_percent
            result.mem_after = health_after.mem_percent
            result.post_check_ok = True
            result.patch_applied = verify_patch_applied(conn, profile, device.patch_file)
            logger.info(f"[{device.hostname}] Post-check: CPU={health_after.cpu_percent}%, MEM={health_after.mem_percent}%, Patch={result.patch_applied}")
        except Exception as e:
            logger.warning(f"[{device.hostname}] Post-check failed: {e}")
        finally:
            conn.disconnect()

    # 生成回退文件
    if applied_cmds:
        _generate_rollback(device, profile, applied_cmds, kwargs.get("run_id", ""))

    # 最终状态判定
    if result.commands_applied > 0 and result.commands_failed == 0:
        result.status = "success"
        _write(device, "update_result", "SUCCESS")
    elif result.commands_applied > 0:
        result.status = "partial"
        _write(device, "update_result", "PARTIAL")
    else:
        result.status = "failed"
        _write(device, "update_result", "FAIL-ACTIVATE")

    result.patch_new = device.patch_file
    result.start_time = start_time
    result.end_time = datetime.now()
    return result


# === 内部辅助函数 ===


def _send_command_interactive(conn, command: str, cmd) -> str:
    """发送交互式命令（自动处理Y/N确认提示）"""
    conn.write_channel(command + "\n")
    output = ""
    loop_count = 0

    while loop_count < MAX_LOOP_ITERATIONS:
        loop_count += 1
        time.sleep(0.5)
        new_output = conn.read_channel()
        output += new_output

        if cmd.expect_pattern and re.search(cmd.expect_pattern, output):
            logger.info(f"Detected confirmation prompt, auto-replying: {cmd.auto_reply}")
            conn.write_channel(cmd.auto_reply + "\n")
            output += conn.read_until_prompt(read_timeout=120)
            break

        if conn.find_prompt() in output:
            break

        if loop_count >= MAX_LOOP_ITERATIONS:
            logger.warning(f"Interactive command loop limit reached, forcing exit")
            break

    return output


def _send_command_wait_progress(conn, command: str, cmd) -> str:
    """发送需要等待进度100%完成的命令（锐捷特有）"""
    conn.write_channel(command + "\n")
    output = ""
    loop_count = 0

    while loop_count < MAX_LOOP_ITERATIONS:
        loop_count += 1
        time.sleep(2)
        new_output = conn.read_channel()
        output += new_output

        if cmd.complete_pattern and re.search(cmd.complete_pattern, output):
            logger.info(f"Progress completed: detected '{cmd.complete_pattern}'")
            try:
                output += conn.read_until_prompt(read_timeout=30)
            except Exception:
                pass
            break

        if cmd.progress_pattern:
            progress_matches = re.findall(cmd.progress_pattern, output)
            if progress_matches:
                logger.debug(f"Current progress: {progress_matches[-1]}")

        if loop_count >= MAX_LOOP_ITERATIONS:
            logger.warning(f"Progress wait loop limit reached, forcing exit")
            break

    return output


def _save_config(conn, profile: VendorProfile) -> None:
    """保存设备配置，自动处理Y/N确认交互"""
    save_cmd = profile.save
    if not save_cmd:
        return

    logger.info(f"Saving config: {save_cmd}")

    if "force" in save_cmd.lower():
        conn.send_command(save_cmd, read_timeout=60)
        return

    conn.write_channel(save_cmd + "\n")
    output = ""
    loop_count = 0

    while loop_count < MAX_LOOP_ITERATIONS:
        loop_count += 1
        time.sleep(0.5)
        new_output = conn.read_channel()
        output += new_output

        if re.search(r"[Yy]/[Nn]", output):
            logger.info("Detected save confirmation prompt, auto-replying: Y")
            conn.write_channel("Y\n")
            try:
                output += conn.read_until_prompt(read_timeout=120)
            except Exception:
                pass
            break

        if conn.find_prompt() in output:
            break

        if loop_count >= MAX_LOOP_ITERATIONS:
            logger.warning(f"Save config loop limit reached, forcing exit")
            break


def _generate_rollback(device, profile, applied_cmds, run_id) -> None:
    """生成回退文件"""
    rollback_dir = Path(device.excel_path).parent / "rollback"
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
    for act_cmd, formatted in reversed(applied_cmds):
        rb_cmd = _find_rollback_cmd(profile, act_cmd.command, device.patch_file)
        lines.append(rb_cmd)

    rollback_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"[{device.hostname}] Rollback file: {rollback_path}")


def _find_rollback_cmd(profile: VendorProfile, activate_cmd: str, patch_file: str) -> str:
    """根据激活命令查找对应的回退命令"""
    for i, act_cmd in enumerate(profile.activate):
        if act_cmd.command == activate_cmd:
            if i < len(profile.rollback):
                return format_command(profile.rollback[i].command, patch_file=patch_file, patch_id=patch_file)
    return f"# No rollback found for: {activate_cmd}"
