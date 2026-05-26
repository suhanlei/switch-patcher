"""
命令行接口模块
- 支持从patch_config.yaml读取配置（Sheet、用户名密码等）
- CLI参数可覆盖配置文件值
- 简化执行：python -m switch_patcher 即可一键执行
- 支持--list-sheets查看可用Sheet
- 支持--dry-run预检查模式
- 支持--gen-config生成示例配置文件
"""

import argparse
import sys
from typing import Optional, List

from switch_patcher.config import load_config, gen_config
from switch_patcher.excel_io import read_devices, list_sheets
from switch_patcher.batch_engine import run_batch
from switch_patcher.reporting import print_report


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """定义并解析命令行参数"""
    parser = argparse.ArgumentParser(
        prog="switch_patcher",
        description="批量交换机补丁工具 - 一条命令完成补丁检查、传输、激活全流程",
    )
    # Excel文件路径（可选，配置文件中也有）
    parser.add_argument("input", nargs="?", default=None, help="输入Excel文件路径（也可在配置文件中指定）")
    # 灰度补丁：指定Sheet名称
    parser.add_argument("--sheet", help="指定Excel Sheet名称（灰度分批），覆盖配置文件值")
    # 查看可用Sheet
    parser.add_argument("--list-sheets", action="store_true", help="列出Excel中所有可用Sheet后退出")
    # 预检查模式
    parser.add_argument("--dry-run", action="store_true", help="仅预检查，不传输文件、不激活补丁")
    # 生成示例配置文件
    parser.add_argument("--gen-config", action="store_true", help="生成示例配置文件 patch_config.yaml")
    # SSH认证信息（覆盖配置文件）
    parser.add_argument("--username", help="SSH用户名（覆盖配置文件值）")
    parser.add_argument("--password", help="SSH密码（覆盖配置文件值）")
    # 执行控制（覆盖配置文件）
    parser.add_argument("--workers", type=int, default=None, help="最大并发设备连接数（默认: 5）")
    parser.add_argument("--timeout", type=int, default=None, help="SSH超时秒数（默认: 30）")
    parser.add_argument("--save", action="store_true", default=None, help="激活后自动保存配置（默认: 不保存）")
    parser.add_argument("--ssh-port", type=int, default=None, help="SSH端口（默认: 22）")
    parser.add_argument("--patches-dir", default=None, help="补丁文件目录（默认: patches）")
    return parser.parse_args(argv)


def _merge_config(args: argparse.Namespace, cfg: dict) -> dict:
    """合并CLI参数与配置文件，CLI参数优先"""
    merged = {
        "excel_path": args.input or cfg.get("excel", ""),
        "sheet": args.sheet or cfg.get("sheet"),
        "username": args.username or cfg.get("username", ""),
        "password": args.password or cfg.get("password", ""),
        "ssh_port": args.ssh_port or cfg.get("ssh_port", 22),
        "workers": args.workers or cfg.get("workers", 5),
        "timeout": args.timeout or cfg.get("timeout", 30),
        "patches_dir": args.patches_dir or cfg.get("patches_dir", "patches"),
        "save": args.save if args.save is not None else cfg.get("save", False),
        "dry_run": args.dry_run or cfg.get("dry_run", False),
    }
    return merged


def main(argv: Optional[List[str]] = None) -> int:
    """主入口函数"""
    args = parse_args(argv)

    # 生成配置文件模式
    if args.gen_config:
        msg = gen_config()
        print(msg)
        return 0

    # 加载配置文件
    cfg = load_config()
    c = _merge_config(args, cfg)

    # 校验必填参数
    if not c["excel_path"]:
        print("ERROR: 请指定Excel文件路径（命令行参数或在patch_config.yaml中配置excel字段）", file=sys.stderr)
        return 1
    if not c["username"] or not c["password"]:
        print("ERROR: 请提供SSH用户名和密码（命令行--username/--password或在patch_config.yaml中配置）", file=sys.stderr)
        return 1

    # 列出Sheet模式
    if args.list_sheets:
        sheets = list_sheets(c["excel_path"])
        print(f"Available sheets in {c['excel_path']}:")
        for s in sheets:
            print(f"  - {s}")
        return 0

    # 从指定Sheet读取设备列表
    devices = read_devices(c["excel_path"], sheet_name=c["sheet"])
    if not devices:
        print("No devices found in the Excel file", file=sys.stderr)
        return 1

    print(f"Loaded {len(devices)} devices from '{c['sheet'] or 'default'}' sheet")

    # 执行批量补丁（分步编排：check_scp→open_scp→recheck→upload→activate→finalize）
    results = run_batch(
        excel_path=c["excel_path"],
        sheet_name=c["sheet"],
        username=c["username"],
        password=c["password"],
        ssh_port=c["ssh_port"],
        timeout=c["timeout"],
        dry_run=c["dry_run"],
        save_after_apply=c["save"],
        max_workers=c["workers"],
        patches_dir=c["patches_dir"],
    )

    return 0
