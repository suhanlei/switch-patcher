"""
批量执行引擎模块 — 分步编排
- 参考ksnetwork的patch_run_all流程，自动分6步执行
- 每步读取Excel获取最新设备状态，基于字段值自动过滤需要执行的设备
- ThreadPoolExecutor实现设备间并发，threading.Lock保护Excel回写
- 步骤1: check_scp   — 检查SCP/SFTP状态 → 写scp_status列
- 步骤2: open_scp    — 只对scp_status=none的设备开启SCP/SFTP
- 步骤3: recheck_scp — 再次检查确认已开启 → 更新scp_status列
- 步骤4: upload      — 只对upload_success≠OK的设备上传补丁
- 步骤5: activate    — 只对upload_success=OK且update_result≠SUCCESS的设备激活补丁
- 步骤6: finalize    — 后检查 + 生成回退文件 + 打印汇总报告
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from switch_patcher.excel_io import DeviceResult, read_devices, list_sheets
from switch_patcher.vendor_profiles import load_profile
from switch_patcher.device_worker import step_check_scp, step_enable_scp, step_upload, step_activate
from switch_patcher.reporting import print_report
from switch_patcher.logger import setup_console_logger, get_device_logger

logger = logging.getLogger("switch_patcher")


def run_batch(
    excel_path: str,
    sheet_name: Optional[str] = None,
    username: str = "",
    password: str = "",
    ssh_port: int = 22,
    timeout: int = 30,
    dry_run: bool = False,
    save_after_apply: bool = False,
    max_workers: int = 5,
    patches_dir: str = "patches",
) -> List[DeviceResult]:
    """
    分步编排执行批量补丁流程
    - 每步重新读取Excel，获取最新设备状态
    - 基于Excel字段值自动过滤设备，无需手动传skip参数
    - 返回: 所有设备的最终执行结果
    """
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logs_dir = str(Path(excel_path).parent / "logs")

    setup_console_logger()
    logger.info(f"=== Patch run {run_id} started ===")

    all_results: List[DeviceResult] = []
    progress_lock = threading.Lock()

    def _run_step(step_name: str, devices: list, step_func, step_kwargs: dict):
        if not devices:
            logger.info(f"Step [{step_name}]: No devices to process, skipping")
            return []

        logger.info(f"Step [{step_name}]: Processing {len(devices)} devices (workers={max_workers})")
        step_results = []
        completed_count = 0
        total = len(devices)

        def _worker(device):
            nonlocal completed_count
            device_logger = get_device_logger(device.hostname, run_id, logs_dir)
            try:
                profile = load_profile(device.vendor)
            except FileNotFoundError as e:
                device_logger.error(f"Vendor profile not found: {e}")
                result = DeviceResult(
                    hostname=device.hostname,
                    mgmt_ip=device.mgmt_ip,
                    vendor=device.vendor,
                    status="failed",
                    error_message=str(e),
                )
                with progress_lock:
                    completed_count += 1
                    step_results.append(result)
                    logger.info(f"[{completed_count}/{total}] {device.hostname} ... FAIL")
                return result

            # 注入公共参数
            kwargs = {**step_kwargs, "username": username, "password": password,
                      "ssh_port": ssh_port, "timeout": timeout}
            result = step_func(device, profile, **kwargs)

            # step_activate 返回 DeviceResult，其他步骤返回简单值
            with progress_lock:
                completed_count += 1
                if isinstance(result, DeviceResult):
                    step_results.append(result)
                    status_str = result.status.upper()
                else:
                    status_str = "OK" if result else "FAIL"
                logger.info(f"[{completed_count}/{total}] {device.hostname} ... {status_str}")

            return result

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_worker, d): d for d in devices}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Worker exception: {e}")

        return step_results

    # ========== 步骤1: 检查SCP/SFTP状态 ==========
    devices = read_devices(excel_path, sheet_name)
    logger.info(f"Loaded {len(devices)} devices from '{sheet_name or 'default'}' sheet")

    # 注入excel_path（sheet_name在read_devices时已填入）
    for d in devices:
        d.excel_path = excel_path

    # 只对scp_status为空的设备检查（已检查过的跳过，包括unreachable/login_fail）
    need_check = [d for d in devices if not d.scp_status]

    _run_step("check_scp", need_check, step_check_scp, {})

    # ========== 步骤2: 开启SCP/SFTP（仅scp_status=none的设备） ==========
    devices = read_devices(excel_path, sheet_name)
    for d in devices:
        d.excel_path = excel_path
    need_open = [d for d in devices if d.scp_status.lower() == "none"]

    _run_step("open_scp", need_open, step_enable_scp, {})

    # ========== 步骤3: 再次检查确认已开启 ==========
    devices = read_devices(excel_path, sheet_name)
    for d in devices:
        d.excel_path = excel_path
    need_recheck = [d for d in devices if d.scp_status.lower() == "none"]

    _run_step("recheck_scp", need_recheck, step_check_scp, {})

    # ========== 步骤4: 上传补丁文件 ==========
    devices = read_devices(excel_path, sheet_name)
    for d in devices:
        d.excel_path = excel_path
    need_upload = [d for d in devices
                   if d.upload_success.upper() != "OK"
                   and d.scp_status.lower() not in ("none", "unreachable", "login_fail")
                   and d.update_result.upper() != "SUCCESS"]

    _run_step("upload", need_upload, step_upload, {"patches_dir": patches_dir})

    # ========== 步骤5: 激活补丁 ==========
    devices = read_devices(excel_path, sheet_name)
    for d in devices:
        d.excel_path = excel_path
    need_activate = [d for d in devices
                     if d.upload_success.upper() == "OK"
                     and d.update_result.upper() != "SUCCESS"
                     and d.scp_status.lower() not in ("unreachable", "login_fail")]

    step_results = _run_step("activate", need_activate, step_activate, {
        "dry_run": dry_run,
        "save": save_after_apply,
        "patches_dir": patches_dir,
        "run_id": run_id,
    })
    all_results.extend(step_results)

    # ========== 步骤6: 汇总报告 ==========
    devices = read_devices(excel_path, sheet_name)
    for d in devices:
        d.excel_path = excel_path
    for d in devices:
        if d.update_result.upper() == "SUCCESS" and not any(r.hostname == d.hostname for r in all_results):
            all_results.append(DeviceResult(
                hostname=d.hostname,
                mgmt_ip=d.mgmt_ip,
                vendor=d.vendor,
                status="skipped",
                patch_applied=True,
                error_message="Already completed in previous run",
            ))

    # 打印汇总统计
    success = sum(1 for r in all_results if r.status == "success")
    partial = sum(1 for r in all_results if r.status == "partial")
    failed = sum(1 for r in all_results if r.status == "failed")
    skipped = sum(1 for r in all_results if r.status == "skipped")

    logger.info(f"=== Run {run_id} complete ===")
    logger.info(f"  Success: {success}  Partial: {partial}  Failed: {failed}  Skipped: {skipped}")

    failed_logins = [r.hostname for r in all_results if r.status == "failed" and
                     ("login_fail" in (r.error_message or "") or "FAIL-LOGIN" in (r.error_message or ""))]
    if failed_logins:
        logger.info(f"  Failed login devices ({len(failed_logins)}): {', '.join(failed_logins)}")

    # 打印格式化报告
    print_report(all_results)
    return all_results
