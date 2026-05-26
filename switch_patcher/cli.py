import argparse
import sys

from switch_patcher.excel_io import read_devices, list_sheets
from switch_patcher.batch_engine import run_batch
from switch_patcher.reporting import print_report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="switch_patcher",
        description="Network switch configuration patch tool - batch apply patches with health checks and rollback generation",
    )
    parser.add_argument("input", help="Path to input Excel file")
    parser.add_argument("--sheet", help="Excel sheet name to process (supports grayscale patching). Use --list-sheets to see available sheets.")
    parser.add_argument("--list-sheets", action="store_true", help="List available sheets in the Excel file and exit")
    parser.add_argument("--dry-run", action="store_true", help="Connect and pre-check only, do not transfer or activate")
    parser.add_argument("--workers", type=int, default=5, help="Max concurrent device connections (default: 5)")
    parser.add_argument("--timeout", type=int, default=30, help="Per-command SSH timeout in seconds (default: 30)")
    parser.add_argument("--save", action="store_true", help="Save config after applying (default: do not save)")
    parser.add_argument("--cpu-threshold", type=float, default=90.0, help="Skip device if CPU exceeds this %% (default: 90)")
    parser.add_argument("--mem-threshold", type=float, default=90.0, help="Skip device if memory exceeds this %% (default: 90)")
    parser.add_argument("--transfer", choices=["sftp", "tftp"], default="sftp", help="File transfer method (default: sftp)")
    parser.add_argument("--username", help="SSH username (unified for all devices)")
    parser.add_argument("--password", help="SSH password (unified for all devices)")
    parser.add_argument("--ssh-port", type=int, default=22, help="SSH port (default: 22)")
    parser.add_argument("--patches-dir", default="patches", help="Directory containing patch files (default: patches)")
    parser.add_argument("--skip-uploaded", action="store_true", help="Skip devices where upload_success=OK")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # List sheets mode
    if args.list_sheets:
        sheets = list_sheets(args.input)
        print(f"Available sheets in {args.input}:")
        for s in sheets:
            print(f"  - {s}")
        return 0

    # Validate required args
    if not args.username or not args.password:
        print("ERROR: --username and --password are required", file=sys.stderr)
        return 1

    # Read devices from selected sheet
    devices = read_devices(args.input, sheet_name=args.sheet)
    if not devices:
        print("No devices found in the Excel file", file=sys.stderr)
        return 1

    print(f"Loaded {len(devices)} devices from '{args.sheet or 'default'}' sheet")

    # Run batch
    results = run_batch(
        devices=devices,
        excel_path=args.input,
        patches_dir=args.patches_dir,
        username=args.username,
        password=args.password,
        ssh_port=args.ssh_port,
        timeout=args.timeout,
        dry_run=args.dry_run,
        save_after_apply=args.save,
        cpu_threshold=args.cpu_threshold,
        mem_threshold=args.mem_threshold,
        max_workers=args.workers,
        skip_uploaded=args.skip_uploaded,
    )

    print_report(results)
    return 0
