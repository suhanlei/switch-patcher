import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

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
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = str(excel_path) if isinstance(excel_path, Path) else excel_path
    logs_dir = str(output_dir) + "/logs" if isinstance(output_dir, str) else "logs"

    setup_console_logger()
    logger.info(f"=== Patch run {run_id} started ===")
    logger.info(f"Total devices: {len(devices)}, Workers: {max_workers}, Dry-run: {dry_run}")

    results: list[DeviceResult] = []
    failed_logins: list[str] = []
    progress_lock = threading.Lock()
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
                results.append(result)
            return result

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
            if result.status == "failed" and "LOGIN" in result.error_message:
                failed_logins.append(device.hostname)
            logger.info(f"[{completed_count}/{total}] {device.hostname} ... {result.status.upper()}")

        return result

    # Skip already-uploaded devices if flag set
    if skip_uploaded:
        before = len(devices)
        devices = [d for d in devices if d.upload_success != "OK"]
        after = len(devices)
        if before != after:
            logger.info(f"Skipped {before - after} already-uploaded devices")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_worker, d): d for d in devices}
        for future in as_completed(futures):
            future.result()  # raise any uncaught exceptions

    # Summary
    success = sum(1 for r in results if r.status == "success")
    partial = sum(1 for r in results if r.status == "partial")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")

    logger.info(f"=== Run {run_id} complete ===")
    logger.info(f"  Success: {success}  Partial: {partial}  Failed: {failed}  Skipped: {skipped}")

    if failed_logins:
        logger.info(f"  Failed login devices ({len(failed_logins)}): {', '.join(failed_logins)}")

    return results
