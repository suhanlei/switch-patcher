import logging
from datetime import datetime
from switch_patcher.excel_io import DeviceResult

logger = logging.getLogger("switch_patcher")


def print_report(results: list[DeviceResult]) -> None:
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
        status_tag = {
            "success": "[OK]",
            "partial": "[PARTIAL]",
            "failed": "[FAIL]",
            "skipped": "[SKIP]",
        }.get(r.status, "[???]")

        if r.status == "success":
            detail = f"{r.commands_applied}/{r.commands_total} commands applied"
        elif r.status == "skipped":
            detail = r.error_message
        else:
            detail = r.error_message[:60]

        print(f"  {status_tag:10s} {r.hostname:<40s} ({r.mgmt_ip}, {r.vendor}) - {detail}")

    # Failed login list
    failed_logins = [r for r in results if r.status == "failed" and "LOGIN" in r.error_message]
    if failed_logins:
        print()
        print("  Failed login devices (re-run will retry these):")
        for r in failed_logins:
            print(f"    - {r.hostname} ({r.mgmt_ip})")

    print("=" * 70)
