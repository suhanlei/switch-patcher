"""
单设备补丁执行步骤模块
- 每个步骤是独立函数：SSH连接→执行→回写Excel→断开连接
- 步骤函数由batch_engine按编排顺序调用
- 每步基于Excel字段判断是否需要执行
- 全局自动处理Y/N确认提示（无需在YAML里逐条配置）
- 支持锐捷3步流程（upgrade→active→running），每步等待进度100%完成
- 死循环保护：所有while循环最多60次迭代后强制退出
"""

import re
import logging
import time
from datetime import datetime
from pathlib import Path

from switch_patcher.vendor_profiles import VendorProfile, format_command
from switch_patcher.connection import create_connection
from switch_patcher.file_transfer import check_connectivity, sftp_upload, verify_file_on_device
from switch_patcher.health_check import run_health_checks, verify_patch_applied, check_scp_sftp
from switch_patcher.excel_io import DeviceInfo, DeviceResult, write_cell, calc_md5
from switch_patcher.logger import get_device_logger

_module_logger = logging.getLogger(__name__)

MAX_LOOP_ITERATIONS = 60

# Y/N确认提示的匹配模式（全局生效，无需在YAML里逐条配置）
_CONFIRM_PATTERN = re.compile(r"\[[Yy]/[Nn]\]\s*:?\s*$")


def _get_logger(device: DeviceInfo, **kwargs) -> logging.Logger:
    """获取设备专属日志器（写入文件+控制台），回退到模块日志器"""
    run_id = kwargs.get("run_id", "")
    logs_dir = kwargs.get("logs_dir", "")
    if run_id and logs_dir:
        return get_device_logger(device.hostname, run_id, logs_dir)
    return _module_logger


def _send_cmd(conn, command: str, read_timeout: int = 30, delay_factor: float = 1.0,
              wait_progress: bool = False, logger=None) -> str:
    """
    统一的命令发送函数
    - 优先使用send_command_timing（兼容H3C Comware）
    - 全局自动检测Y/N确认提示并回复Y
    - 自动去掉设备回显的命令本身
    - wait_progress=True时持续等待直到出现设备提示符
    """
    if logger is None:
        logger = _module_logger

    try:
        output = conn.send_command_timing(
            command, read_timeout=read_timeout, delay_factor=delay_factor, max_loops=500,
        )
    except Exception:
        try:
            output = conn.send_command(
                command, read_timeout=read_timeout, delay_factor=delay_factor, max_loops=500,
            )
        except Exception as e:
            logger.warning(f"Command '{command}' failed: {e}")
            return ""

    # 检测Y/N确认提示，自动回复Y并继续等待
    if _CONFIRM_PATTERN.search(output):
        logger.info(f"Auto-replying Y to confirmation prompt")
        conn.write_channel("Y\n")
        try:
            more = conn.read_until_prompt(read_timeout=read_timeout)
            output += more
        except Exception:
            time.sleep(3)
            try:
                output += conn.read_channel()
            except Exception:
                pass

    # 进度等待模式：持续读取直到出现设备提示符
    if wait_progress:
        loop = 0
        while loop < MAX_LOOP_ITERATIONS:
            loop += 1
            time.sleep(2)
            try:
                more = conn.read_channel()
                if more:
                    output += more
                    logger.debug(f"Progress reading: +{len(more)} bytes")
            except Exception:
                pass
            # 检测设备提示符（命令执行完毕）
            try:
                if conn.find_prompt() and conn.find_prompt().strip() in output[-50:]:
                    logger.info("Progress completed, prompt detected")
                    break
            except Exception:
                pass
            if loop >= MAX_LOOP_ITERATIONS:
                logger.warning(f"Progress wait limit ({MAX_LOOP_ITERATIONS}) reached")
                break

    return _strip_echo(output, command)


def _strip_echo(output: str, command: str) -> str:
    """去掉设备回显的命令本身，只保留真正的响应内容"""
    if not output or not command:
        return output
    cmd_stripped = command.strip()
    lines = output.split('\n')
    # 只去掉第一行匹配命令回显的行
    if lines:
        first = lines[0].strip().lstrip('>').lstrip('#').strip()
        if first == cmd_stripped or first.startswith(cmd_stripped):
            lines = lines[1:]
    # 去掉末尾的设备提示符
    if lines:
        last = lines[-1].strip()
        # 提示符通常是 <hostname> 或 [hostname] 格式
        if (last.startswith('<') and last.endswith('>')) or \
           (last.startswith('[') and last.endswith(']')):
            lines = lines[:-1]
    result = '\n'.join(lines).strip()
    return result if result else output


def _write(device: DeviceInfo, col_name: str, value: str):
    write_cell(device.excel_path, device.row_index, col_name, value,
               sheet_name=device.sheet_name)


def _connect_with_retry(device, profile, username, password, ssh_port, timeout, max_retries=3, logger=None):
    if logger is None:
        logger = _module_logger
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
    username = kwargs.get("username", "")
    password = kwargs.get("password", "")
    ssh_port = kwargs.get("ssh_port", 22)
    timeout = kwargs.get("timeout", 30)
    log = _get_logger(device, **kwargs)

    log.info(f"[{device.hostname}] Step check_scp: checking TCP connectivity on {device.mgmt_ip}:{ssh_port}")

    if not check_connectivity(device.mgmt_ip, ssh_port):
        log.warning(f"[{device.hostname}] TCP unreachable on {device.mgmt_ip}:{ssh_port}")
        _write(device, "scp_status", "unreachable")
        _write(device, "update_result", "FAIL-UNREACHABLE")
        return "unreachable"

    conn = _connect_with_retry(device, profile, username, password, ssh_port, timeout, logger=log)
    if not conn:
        return "login_fail"

    try:
        status = check_scp_sftp(conn, profile)
        _write(device, "scp_status", status)
        log.info(f"[{device.hostname}] SCP/SFTP status: {status}")
        return status
    except Exception as e:
        log.error(f"[{device.hostname}] Check SCP exception: {e}")
        _write(device, "scp_status", "none")
        return "none"
    finally:
        conn.disconnect()


def step_enable_scp(device: DeviceInfo, profile: VendorProfile, **kwargs) -> bool:
    username = kwargs.get("username", "")
    password = kwargs.get("password", "")
    ssh_port = kwargs.get("ssh_port", 22)
    timeout = kwargs.get("timeout", 30)
    log = _get_logger(device, **kwargs)

    log.info(f"[{device.hostname}] Step open_scp: enabling SCP/SFTP services")

    conn = _connect_with_retry(device, profile, username, password, ssh_port, timeout, logger=log)
    if not conn:
        return False

    try:
        for scp_cmd in profile.scp_enable_commands:
            formatted = format_command(scp_cmd.command)
            log.info(f"[{device.hostname}] Sending: {formatted}")
            output = _send_cmd(conn, formatted, read_timeout=30, logger=log)
            log.info(f"[{device.hostname}] Response: {output[:200] if output else '(empty)'}")

        _save_config(conn, profile, log)
        log.info(f"[{device.hostname}] SCP/SFTP enabled and saved")
        return True
    except Exception as e:
        log.error(f"[{device.hostname}] Enable SCP exception: {e}")
        return False
    finally:
        conn.disconnect()


def step_upload(device: DeviceInfo, profile: VendorProfile, **kwargs) -> bool:
    username = kwargs.get("username", "")
    password = kwargs.get("password", "")
    ssh_port = kwargs.get("ssh_port", 22)
    timeout = kwargs.get("timeout", 30)
    patches_dir = kwargs.get("patches_dir", "patches")
    log = _get_logger(device, **kwargs)

    local_patch_path = Path(patches_dir) / device.patch_file
    if not local_patch_path.exists():
        log.error(f"[{device.hostname}] Patch file not found: {local_patch_path}")
        _write(device, "update_result", "FAIL-NOFILE")
        return False

    md5_local = calc_md5(str(local_patch_path))
    log.info(f"[{device.hostname}] Step upload: local file={local_patch_path}, size={local_patch_path.stat().st_size}, md5={md5_local}")
    _write(device, "md5_base", md5_local)

    if device.scp_status.lower() in ("unreachable", "login_fail"):
        log.warning(f"[{device.hostname}] Skip upload: scp_status={device.scp_status}")
        return False

    remote_path = f"{profile.remote_dir}{device.patch_file}"
    log.info(f"[{device.hostname}] Uploading to {remote_path} via SFTP")
    upload_ok = sftp_upload(device, profile, str(local_patch_path), remote_path, username, password, ssh_port)
    if not upload_ok:
        log.error(f"[{device.hostname}] SFTP upload failed")
        _write(device, "upload_success", "FAIL")
        _write(device, "update_result", "FAIL-UPLOAD")
        return False

    log.info(f"[{device.hostname}] SFTP upload OK, verifying file integrity")

    conn = _connect_with_retry(device, profile, username, password, ssh_port, timeout, logger=log)
    if not conn:
        _write(device, "upload_success", "FAIL-VERIFY")
        return False

    try:
        verified, verify_val = verify_file_on_device(conn, profile, device.patch_file, md5_local)
        if verified:
            _write(device, "upload_success", "OK")
            _write(device, "md5_uploaded", verify_val)
            log.info(f"[{device.hostname}] Upload verified OK ({profile.verify_method}: {verify_val})")
            return True
        else:
            log.error(f"[{device.hostname}] Upload verification FAILED ({profile.verify_method}: expected={md5_local}, got={verify_val})")
            _write(device, "upload_success", "FAIL-MD5")
            _write(device, "update_result", "FAIL-MD5")
            return False
    except Exception as e:
        log.error(f"[{device.hostname}] Verify exception: {e}")
        _write(device, "upload_success", "FAIL-VERIFY")
        return False
    finally:
        conn.disconnect()


def step_activate(device: DeviceInfo, profile: VendorProfile, **kwargs) -> DeviceResult:
    username = kwargs.get("username", "")
    password = kwargs.get("password", "")
    ssh_port = kwargs.get("ssh_port", 22)
    timeout = kwargs.get("timeout", 30)
    dry_run = kwargs.get("dry_run", False)
    save_after_apply = kwargs.get("save", False)
    log = _get_logger(device, **kwargs)

    result = DeviceResult(
        hostname=device.hostname,
        mgmt_ip=device.mgmt_ip,
        vendor=device.vendor,
    )
    start_time = datetime.now()

    # 单连接: 预检查 + 激活 + 后检查
    log.info(f"[{device.hostname}] Step activate: connecting (dry_run={dry_run})")
    conn = _connect_with_retry(device, profile, username, password, ssh_port, timeout, logger=log)
    if not conn:
        result.status = "failed"
        result.error_message = "SSH login failed for activation"
        result.start_time = start_time
        result.end_time = datetime.now()
        return result

    applied_cmds = []
    try:
        # 预检查
        log.info(f"[{device.hostname}] Pre-check: running health checks")
        health_before = run_health_checks(conn, profile, expected_patch_file=device.patch_file)
        result.patch_now = health_before.patch_id or ""
        log.info(f"[{device.hostname}] Pre-check: patch_now={result.patch_now or '(unknown)'}, already_active={health_before.patch_already_active}")
        if health_before.raw_output:
            for key, val in health_before.raw_output.items():
                log.info(f"[{device.hostname}] Pre-check [{key}]: {val[:300] if val else '(empty)'}")
        _write(device, "patch_now", result.patch_now)
        _write(device, "patch_new", device.patch_file)

        # 目标补丁已激活，跳过
        if health_before.patch_already_active:
            log.warning(f"[{device.hostname}] Pre-check: patch {device.patch_file} already active, skipping")
            _write(device, "update_result", "SKIP-already active")
            result.status = "skipped"
            result.error_message = "Patch already active"
            result.start_time = start_time
            result.end_time = datetime.now()
            return result

        result.pre_check_ok = True
        log.info(f"[{device.hostname}] Pre-check OK")

        # dry_run模式到此结束
        if dry_run:
            log.info(f"[{device.hostname}] Dry-run complete, skipping activation")
            _write(device, "update_result", "DRYRUN-OK")
            result.status = "skipped"
            result.start_time = start_time
            result.end_time = datetime.now()
            return result

        # 激活补丁（复用同一连接）
        log.info(f"[{device.hostname}] Executing patch commands (config_mode={profile.config_mode_required})")
        if profile.config_mode_required:
            log.info(f"[{device.hostname}] Entering config mode")
            conn.config_mode()
        else:
            log.info(f"[{device.hostname}] Staying in user view (config_mode_required=false)")

        for cmd in profile.activate:
            formatted = format_command(cmd.command, patch_file=device.patch_file, patch_id=device.patch_file)
            log.info(f"[{device.hostname}] Sending: {formatted}")
            try:
                output = _send_cmd(
                    conn, formatted,
                    read_timeout=120,
                    delay_factor=4,
                    wait_progress=cmd.wait_progress,
                    logger=log,
                )

                has_error = any(re.search(pat, output, re.IGNORECASE) for pat in profile.error_patterns)
                if has_error:
                    matched = [pat for pat in profile.error_patterns if re.search(pat, output, re.IGNORECASE)]
                    log.error(f"[{device.hostname}] Command ERROR [{formatted}]: matched={matched}")
                    result.commands_failed += 1
                else:
                    log.info(f"[{device.hostname}] Command OK: {formatted}")
                    applied_cmds.append((cmd, formatted))
                    result.commands_applied += 1
            except Exception as e:
                log.error(f"[{device.hostname}] Command exception [{formatted}]: {e}")
                result.commands_failed += 1
            result.commands_total += 1

        if profile.config_mode_required:
            log.info(f"[{device.hostname}] Exiting config mode")
            conn.exit_config_mode()

        if save_after_apply:
            log.info(f"[{device.hostname}] Saving configuration...")
            _save_config(conn, profile, log)

        # 后检查（复用同一连接，激活命令完成后设备状态已更新）
        log.info(f"[{device.hostname}] Post-check: verifying patch applied")
        result.post_check_ok = True
        result.patch_applied = verify_patch_applied(conn, profile, device.patch_file)
        log.info(f"[{device.hostname}] Post-check: patch_applied={result.patch_applied}")
    except Exception as e:
        log.error(f"[{device.hostname}] Activate phase exception: {e}")
    finally:
        if conn:
            conn.disconnect()

    # 回退文件
    if applied_cmds:
        _generate_rollback(device, profile, applied_cmds, kwargs.get("run_id", ""), result.patch_now, log)

    # 状态判定
    if result.commands_applied > 0 and result.commands_failed == 0:
        result.status = "success"
        _write(device, "update_result", "SUCCESS")
        log.info(f"[{device.hostname}] ACTIVATION SUCCESS ({result.commands_applied}/{result.commands_total} commands)")
    elif result.commands_applied > 0:
        result.status = "partial"
        _write(device, "update_result", "PARTIAL")
        log.warning(f"[{device.hostname}] ACTIVATION PARTIAL ({result.commands_applied}/{result.commands_total} commands OK)")
    else:
        result.status = "failed"
        _write(device, "update_result", "FAIL-ACTIVATE")
        log.error(f"[{device.hostname}] ACTIVATION FAILED (0/{result.commands_total} commands OK)")

    result.patch_new = device.patch_file
    result.start_time = start_time
    result.end_time = datetime.now()
    return result


# === 内部辅助函数 ===


def _save_config(conn, profile: VendorProfile, logger) -> None:
    save_cmd = profile.save
    if not save_cmd:
        return

    logger.info(f"Saving config: {save_cmd}")

    if "force" in save_cmd.lower():
        output = _send_cmd(conn, save_cmd, read_timeout=60, logger=logger)
        logger.info(f"Save force: {output[:200] if output else '(empty)'}")
        return

    # 非force的save可能也有Y/N确认，_send_cmd会自动处理
    output = _send_cmd(conn, save_cmd, read_timeout=120, delay_factor=2, logger=logger)
    logger.info(f"Save config: {output[:200] if output else '(empty)'}")


def _generate_rollback(device, profile, applied_cmds, run_id, patch_old, logger) -> None:
    rollback_dir = Path(device.excel_path).parent / "rollback"
    rollback_dir.mkdir(exist_ok=True)
    rollback_path = rollback_dir / f"{device.hostname}_{run_id}.txt"

    lines = [
        f"# Rollback commands for: {device.hostname} ({device.mgmt_ip})",
        f"# Vendor: {device.vendor}",
        f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Patch file: {device.patch_file}",
        f"# Previous patch: {patch_old or '(none)'}",
        f"# Run ID: {run_id}",
        f"#",
        f"# Prerequisite: old patch file must exist on device flash.",
        f"#   1. Run 'dir flash:/{patch_old}' to check.",
        f"#   2. If missing, upload via SFTP before executing rollback.",
        f"#",
        f"# WARNING: Review before executing. Apply in the order listed.",
        "",
    ]
    for rb_cmd in profile.rollback:
        formatted = format_command(rb_cmd.command, patch_file=device.patch_file, patch_id=device.patch_file, patch_old=patch_old)
        lines.append(formatted)

    rollback_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"[{device.hostname}] Rollback file: {rollback_path}")
