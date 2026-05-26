"""
执行报告模块
- 汇总所有设备的执行结果
- 在控制台打印格式化的摘要报告
- 单独列出登录失败的设备，方便二次重试
"""

import logging
from datetime import datetime
from typing import List

from switch_patcher.excel_io import DeviceResult

logger = logging.getLogger("switch_patcher")


def print_report(results: List[DeviceResult]) -> None:
    """打印批量执行的汇总报告"""
    total = len(results)
    success = sum(1 for r in results if r.status == "success")
    partial = sum(1 for r in results if r.status == "partial")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")

    print()
    print("=" * 70)
    print(f"  Patch Summary - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print(f"  Total: {total}  |  Success: {success}  |  Partial: {partial}  |  Failed: {failed}  |  Skipped: {skipped}")
    print("-" * 70)

    for r in results:
        # 状态标签
        status_tag = {
            "success": "[OK]",
            "partial": "[PARTIAL]",
            "failed": "[FAIL]",
            "skipped": "[SKIP]",
        }.get(r.status, "[???]")

        # 详情描述
        if r.status == "success":
            detail = f"{r.commands_applied}/{r.commands_total} commands applied"
        elif r.status == "skipped":
            detail = r.error_message
        else:
            detail = r.error_message[:60]  # 截断过长的错误信息

        print(f"  {status_tag:10s} {r.hostname:<40s} ({r.mgmt_ip}, {r.vendor}) - {detail}")

    # 单独列出登录失败的设备
    failed_logins = [r for r in results if r.status == "failed" and
                     ("login_fail" in (r.error_message or "") or "FAIL-LOGIN" in (r.error_message or ""))]
    if failed_logins:
        print()
        print("  Failed login devices (re-run will retry these):")
        for r in failed_logins:
            print(f"    - {r.hostname} ({r.mgmt_ip})")

    print("=" * 70)
