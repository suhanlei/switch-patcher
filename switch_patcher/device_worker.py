"""
单设备补丁执行流程模块
- 包含完整的6阶段流程：本地校验 → 预检查 → 文件传输 → 激活 → 后检查 → 生成回退
- 每个阶段独立建立/断开SSH连接，避免长连接超时
- 每个阶段完成后立即回写Excel，支持断点续跑
- SSH登录失败自动3次重试，间隔3秒
- 已成功的设备自动跳过，只重试失败设备
- 支持交互式Y/N确认自动回复（H3C install activate、华为 patch load）
- 支持锐捷3步补丁流程（upgrade→active→running），每步等待100%完成
- 死循环保护：所有while循环最多60次迭代后强制退出
- 支持SCP/SFTP使能前置步骤
- 保存配置时处理Y/N确认交互
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

# 死循环保护：while True循环最大迭代次数，防止设备输出异常导致无限等待
MAX_LOOP_ITERATIONS = 60


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
    enable_scp: bool = True,
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
    - enable_scp: 是否在文件传输前自动启用SCP/SFTP服务
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
        # 如果需要，先启用SCP/SFTP服务
        if enable_scp and profile.scp_enable_commands:
            logger.info(f"[{device.hostname}] Enabling SCP/SFTP services before transfer")
            scp_conn = None
            for attempt in range(1, 4):
                try:
                    scp_conn = create_connection(device, profile, username, password, ssh_port, timeout)
                    break
                except ConnectionError:
                    if attempt == 3:
                        logger.error(f"[{device.hostname}] Cannot connect for SCP enable")
                        break
                    time.sleep(3)

            if scp_conn:
                try:
                    _enable_scp_sftp(scp_conn, profile)
                except Exception as e:
                    logger.warning(f"[{device.hostname}] SCP enable failed (may already be enabled): {e}")
                finally:
                    scp_conn.disconnect()

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
            # 在设备端执行MD5/文件大小校验命令，比对本地和远端
            verified, verify_val = verify_file_on_device(conn, profile, device.patch_file, md5_local)
            if verified:
                write_cell(excel_path, device.row_index, "upload_success", "OK")
                write_cell(excel_path, device.row_index, "md5_uploaded", verify_val)
                result.transfer_ok = True
            else:
                # 校验不匹配，文件可能损坏，不进入激活阶段
                write_cell(excel_path, device.row_index, "upload_success", "FAIL-MD5")
                write_cell(excel_path, device.row_index, "update_result", "FAIL-MD5")
                result.status = "failed"
                result.error_message = f"File verification failed: {verify_val}"
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
                # 根据命令类型选择不同执行方式
                if cmd.wait_progress:
                    # 锐捷3步流程：等待进度100%完成
                    output = _send_command_wait_progress(conn, formatted, cmd)
                elif cmd.expect_pattern:
                    # 交互式命令：H3C install activate、华为 patch load 会提示Y/N确认
                    output = _send_command_interactive(conn, formatted, cmd)
                else:
                    # 普通命令：直接发送
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
            _save_config(conn, profile)
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


def _send_command_interactive(conn, command: str, cmd) -> str:
    """
    发送交互式命令（自动处理Y/N确认提示）
    - 先发送命令，然后检测输出中是否出现expect_pattern
    - 如果匹配到确认提示，自动回复auto_reply（如"Y"）
    - 使用死循环保护（最多60次迭代）
    - 适用于H3C的install activate和华为的patch load命令
    """
    # 发送命令但不等待完成（可能需要交互）
    conn.write_channel(command + "\n")
    output = ""
    loop_count = 0

    while loop_count < MAX_LOOP_ITERATIONS:
        loop_count += 1
        time.sleep(0.5)
        # 读取当前可用输出
        new_output = conn.read_channel()
        output += new_output

        # 检查是否出现确认提示（如[Y/N]或(y/n)）
        if cmd.expect_pattern and re.search(cmd.expect_pattern, output):
            logger.info(f"Detected confirmation prompt, auto-replying: {cmd.auto_reply}")
            conn.write_channel(cmd.auto_reply + "\n")
            # 继续等待命令执行完成
            output += conn.read_until_prompt(read_timeout=120)
            break

        # 检查是否已出现命令提示符（命令已执行完毕）
        if conn.find_prompt() in output:
            break

        # 死循环保护：超过最大迭代次数强制退出
        if loop_count >= MAX_LOOP_ITERATIONS:
            logger.warning(f"Interactive command loop limit reached ({MAX_LOOP_ITERATIONS}), forcing exit")
            break

    return output


def _send_command_wait_progress(conn, command: str, cmd) -> str:
    """
    发送需要等待进度100%完成的命令（锐捷特有）
    - 发送命令后持续读取输出，检测进度百分比
    - 当输出中出现complete_pattern（如"100%"）时认为完成
    - 使用死循环保护（最多60次迭代，每次等待2秒，总计约2分钟）
    - 适用于锐捷的upgrade/patch active/patch running命令
    """
    # 发送命令
    conn.write_channel(command + "\n")
    output = ""
    loop_count = 0

    while loop_count < MAX_LOOP_ITERATIONS:
        loop_count += 1
        time.sleep(2)
        new_output = conn.read_channel()
        output += new_output

        # 检查进度百分比是否达到100%
        if cmd.complete_pattern and re.search(cmd.complete_pattern, output):
            logger.info(f"Progress completed: detected '{cmd.complete_pattern}' in output")
            # 等待命令提示符出现
            output += conn.read_until_prompt(read_timeout=30)
            break

        # 记录当前进度（用于调试）
        if cmd.progress_pattern:
            progress_matches = re.findall(cmd.progress_pattern, output)
            if progress_matches:
                logger.debug(f"Current progress: {progress_matches[-1]}")

        # 死循环保护：超过最大迭代次数强制退出
        if loop_count >= MAX_LOOP_ITERATIONS:
            logger.warning(f"Progress wait loop limit reached ({MAX_LOOP_ITERATIONS}), forcing exit")
            break

    return output


def _enable_scp_sftp(conn, profile: VendorProfile) -> None:
    """
    在设备上启用SCP/SFTP服务（补丁文件传输的前置条件）
    - H3C: system-view → scp server enable → sftp server enable
    - 华为: system-view → scp server enable → sftp server enable → commit
    - 锐捷: configure terminal → ip scp server enable
    - 执行完成后自动保存配置
    """
    for scp_cmd in profile.scp_enable_commands:
        formatted = format_command(scp_cmd.command)
        logger.info(f"Enabling SCP/SFTP: {formatted}")
        conn.send_command(formatted, read_timeout=30)

    # SCP使能后保存配置（处理Y/N确认交互）
    _save_config(conn, profile)


def _save_config(conn, profile: VendorProfile) -> None:
    """
    保存设备配置，自动处理Y/N确认交互
    - H3C: save force（无交互）
    - 华为: save → 提示[Y/N] → 自动回答Y
    - 锐捷: write → 提示[Y/N] → 自动回答Y
    - 使用死循环保护（最多60次迭代）
    """
    save_cmd = profile.save
    if not save_cmd:
        return

    logger.info(f"Saving config: {save_cmd}")

    # 如果命令中包含force（如H3C的save force），无需交互
    if "force" in save_cmd.lower():
        conn.send_command(save_cmd, read_timeout=60)
        return

    # 其他厂商的save/write命令会提示确认，需要交互处理
    conn.write_channel(save_cmd + "\n")
    output = ""
    loop_count = 0

    while loop_count < MAX_LOOP_ITERATIONS:
        loop_count += 1
        time.sleep(0.5)
        new_output = conn.read_channel()
        output += new_output

        # 检查是否出现Y/N确认提示
        if re.search(r"[Yy]/[Nn]", output):
            logger.info("Detected save confirmation prompt, auto-replying: Y")
            conn.write_channel("Y\n")
            # 等待保存完成
            try:
                output += conn.read_until_prompt(read_timeout=120)
            except Exception:
                pass
            break

        # 检查是否已出现命令提示符（保存已完成）
        if conn.find_prompt() in output:
            break

        # 死循环保护
        if loop_count >= MAX_LOOP_ITERATIONS:
            logger.warning(f"Save config loop limit reached ({MAX_LOOP_ITERATIONS}), forcing exit")
            break


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
