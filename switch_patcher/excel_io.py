import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl

LOCK = threading.Lock()

COLUMN_MAP = {
    "hostname": "Hostname",
    "mgmt_ip": "Mgmt_IP",
    "vendor": "Vendor",
    "patch_now": "patch_now",
    "patch_new": "patch_new",
    "patch_file": "patch_file",
    "md5_base": "patch1_md5_base",
    "md5_uploaded": "patch1_md5_uploaded",
    "login_mode": "login_mode",
    "upload_success": "upload_success",
    "update_result": "update_result",
}


@dataclass
class DeviceInfo:
    hostname: str
    mgmt_ip: str
    vendor: str
    patch_file: str
    row_index: int  # 1-based row in Excel
    patch_now: str = ""
    patch_new: str = ""
    md5_base: str = ""
    md5_uploaded: str = ""
    login_mode: str = ""
    upload_success: str = ""
    update_result: str = ""


@dataclass
class DeviceResult:
    hostname: str
    mgmt_ip: str
    vendor: str
    status: str = "pending"  # success / partial / failed / skipped
    pre_check_ok: bool = False
    transfer_ok: bool = False
    patch_applied: bool = False
    post_check_ok: bool = False
    commands_total: int = 0
    commands_applied: int = 0
    commands_failed: int = 0
    cpu_before: float | None = None
    cpu_after: float | None = None
    mem_before: float | None = None
    mem_after: float | None = None
    patch_now: str = ""
    patch_new: str = ""
    error_message: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None


def list_sheets(excel_path: str) -> list[str]:
    wb = openpyxl.load_workbook(excel_path, read_only=True)
    names = wb.sheetnames[:]
    wb.close()
    return names


def read_devices(excel_path: str, sheet_name: str | None = None) -> list[DeviceInfo]:
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    if sheet_name:
        if sheet_name not in wb.sheetnames:
            wb.close()
            raise ValueError(f"Sheet '{sheet_name}' not found. Available: {wb.sheetnames}")
        ws = wb[sheet_name]
    else:
        ws = wb.active

    headers = [cell.value for cell in ws[1]]
    col_indices = {}
    for idx, h in enumerate(headers, 1):
        if h and h in COLUMN_MAP.values():
            col_indices[h] = idx

    devices = []
    for row_idx in range(2, ws.max_row + 1):
        hostname = ws.cell(row=row_idx, column=col_indices.get("Hostname", 1)).value
        mgmt_ip = ws.cell(row=row_idx, column=col_indices.get("Mgmt_IP", 2)).value
        vendor = ws.cell(row=row_idx, column=col_indices.get("Vendor", 3)).value
        patch_file = ws.cell(row=row_idx, column=col_indices.get("patch_file", 6)).value
        patch_now = ws.cell(row=row_idx, column=col_indices.get("patch_now", 4)).value
        upload_success = ws.cell(row=row_idx, column=col_indices.get("upload_success", 10)).value
        update_result = ws.cell(row=row_idx, column=col_indices.get("update_result", 11)).value

        if not hostname or not mgmt_ip:
            continue

        devices.append(DeviceInfo(
            hostname=str(hostname).strip(),
            mgmt_ip=str(mgmt_ip).strip(),
            vendor=str(vendor).strip() if vendor else "",
            patch_file=str(patch_file).strip() if patch_file else "",
            row_index=row_idx,
            patch_now=str(patch_now).strip() if patch_now else "",
            upload_success=str(upload_success).strip() if upload_success else "",
            update_result=str(update_result).strip() if update_result else "",
        ))

    wb.close()
    return devices


def write_cell(excel_path: str, row_index: int, col_name: str, value: str):
    col_key = COLUMN_MAP.get(col_name)
    if not col_key:
        return

    with LOCK:
        wb = openpyxl.load_workbook(excel_path)
        ws = wb.active

        headers = [cell.value for cell in ws[1]]
        col_idx = None
        for idx, h in enumerate(headers, 1):
            if h == col_key:
                col_idx = idx
                break

        if col_idx is None:
            col_idx = len(headers) + 1
            ws.cell(row=1, column=col_idx, value=col_key)

        ws.cell(row=row_index, column=col_idx, value=value)
        wb.save(excel_path)
        wb.close()


def calc_md5(file_path: str) -> str:
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
