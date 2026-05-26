"""
批量执行引擎模块
- 使用ThreadPoolExecutor实现设备间并发
- 线程安全的进度计数器（threading.Lock保护）
- 失败登录设备单独汇总，方便二次重试
- 支持--skip-uploaded跳过已上传设备的文件传输阶段
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from switch_patcher.excel_io import DeviceResult, list_sheets
from switch_patcher.vendor_profiles import load_profile
from switch_patcher.device_worker import execute_device
from switch_patcher.logger import setup_console_logger, get_device_logger

logger = logging.getLogger("switch_patcher")


def run_batch(
    devices: list,
    excel_path: str,
    patches_dir: str,
    username: str = "",
    password: str = "",
    ssh_port: int = 22,
    timeout: int = 30,
    dry_run: bool = False,
    save_after_apply: bool = False,
    cpu_threshold: float = 90.0,
    mem_threshold: float = 90.0,
    max_workers: int = 5,
    skip_uploaded: bool = False,
) -> list[DeviceResult]:
    """
    批量执行补丁流程
    - devices: 设备信息列表
    - max_workers: 最大并发线程数
    - skip_uploaded: 跳过已上传文件成功的设备
    - 返回: 所有设备的执行结果列表
    """
    # 生成批次ID，用于日志文件和回退文件命名
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logs_dir = str(Path(excel_path).parent / "logs")

    setup_console_logger()
    logger.info(f"=== Patch run {run_id} started ===")
    logger.info(f"Total devices: {len(devices)}, Workers: {max_workers}, Dry-run: {dry_run}")

    results: list[DeviceResult] = []
    failed_logins: list[str] = []       # 登录失败的设备名列表
    progress_lock = threading.Lock()     # 进度计数器锁
    completed_count = 0
    total = len(devices)

    def _worker(device):
        """单个设备的工作线程函数"""
        nonlocal completed_count
        # 为每台设备创建独立的日志文件
        device_logger = get_device_logger(device.hostname, run_id, logs_dir)

        # 加载对应厂商的命令模板
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
                results.append(result)
            return result

        # 执行单设备完整流程
        result = execute_device(
            device=device,
            profile=profile,
            excel_path=excel_path,
            patches_dir=patches_dir,
            run_id=run_id,
            username=username,
            password=password,
            ssh_port=ssh_port,
            timeout=timeout,
            dry_run=dry_run,
            save_after_apply=save_after_apply,
            cpu_threshold=cpu_threshold,
            mem_threshold=mem_threshold,
        )

        with progress_lock:
            completed_count += 1
            results.append(result)
            # 登录失败的设备加入失败列表，方便后续重试
            if result.status == "failed" and "LOGIN" in result.error_message:
                failed_logins.append(device.hostname)
            # 实时打印进度
            logger.info(f"[{completed_count}/{total}] {device.hostname} ... {result.status.upper()}")

        return result

    # 跳过已上传文件成功的设备（配合--skip-uploaded参数）
    if skip_uploaded:
        before = len(devices)
        devices = [d for d in devices if d.upload_success != "OK"]
        after = len(devices)
        if before != after:
            logger.info(f"Skipped {before - after} already-uploaded devices")

    # 提交所有设备到线程池并发执行
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_worker, d): d for d in devices}
        for future in as_completed(futures):
            future.result()  # 等待所有任务完成，抛出未捕获异常

    # 打印汇总统计
    success = sum(1 for r in results if r.status == "success")
    partial = sum(1 for r in results if r.status == "partial")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")

    logger.info(f"=== Run {run_id} complete ===")
    logger.info(f"  Success: {success}  Partial: {partial}  Failed: {failed}  Skipped: {skipped}")

    # 输出登录失败的设备列表
    if failed_logins:
        logger.info(f"  Failed login devices ({len(failed_logins)}): {', '.join(failed_logins)}")

    return results
