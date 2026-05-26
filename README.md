# Switch Patcher

批量给多厂商交换机打系统补丁的Python工具。支持H3C（Comware）、华为（VRP）、锐捷三大厂商，通过SSH连接设备，SFTP传输补丁文件，自动完成补丁激活前后健康检查，生成回退命令文件。

## 功能特性

- **多厂商支持** — H3C / 华为 / 锐捷，命令流程通过YAML模板定义，可直接编辑适配
- **Excel驱动** — 设备列表和执行进度都在同一个Excel中，工具实时回写状态列
- **灰度补丁** — 支持选择Excel中的不同Sheet，分批次执行
- **文件传输** — SFTP上传补丁文件，上传后MD5校验确保完整性
- **健康检查** — 打补丁前后检查CPU/内存/设备状态，超阈值自动跳过
- **登录重试** — 连通性前置检查 + SSH登录失败3次重试
- **断点续跑** — 重复执行自动跳过已成功的设备，只重试失败的
- **并发执行** — ThreadPoolExecutor设备间并发，进度实时显示
- **回退文件** — 为每台设备生成反序undo命令文件，人工确认后手动执行
- **默认不保存** — 补丁激活后默认不save，需`--save`显式开启

## 项目结构

```
switch-patcher/
├── switch_patcher/          # 主程序包
│   ├── __init__.py           # 版本号
│   ├── __main__.py           # python -m switch_patcher 入口
│   ├── cli.py                # 命令行参数定义
│   ├── excel_io.py           # Excel读写 + MD5计算
│   ├── vendor_profiles.py    # YAML厂商模板加载
│   ├── connection.py         # netmiko SSH连接 + 重试
│   ├── file_transfer.py      # SFTP上传 + 连通性检查 + 文件校验
│   ├── health_check.py       # CPU/内存/补丁版本解析
│   ├── device_worker.py      # 单设备五阶段完整流程
│   ├── batch_engine.py       # 并发调度 + 进度追踪
│   ├── reporting.py          # 执行汇总报告
│   └── logger.py             # per-device日志 + 控制台
├── vendor_templates/         # 可编辑的YAML命令模板
│   ├── h3c.yaml
│   ├── huawei.yaml
│   └── ruijie.yaml
├── patches/                  # 补丁文件存放目录
├── venv/                     # Python虚拟环境
├── requirements.txt         # 依赖清单
└── .gitignore
```

运行时自动创建 `logs/`、`backups/`、`rollback/` 目录。

## 环境准备

### 1. Python版本

需要 Python 3.12+。确认版本：

```bash
python --version
```

### 2. 创建虚拟环境

```bash
cd D:\workspace\switch-patcher
python -m venv venv
```

### 3. 激活虚拟环境

Windows CMD：

```cmd
venv\Scripts\activate
```

Windows PowerShell：

```powershell
venv\Scripts\Activate.ps1
```

Git Bash / MSYS2：

```bash
source venv/Scripts/activate
```

激活后命令行提示符会显示 `(venv)` 前缀。

### 4. 安装依赖

```bash
pip install -r requirements.txt
```

依赖列表：

| 包 | 用途 |
|---|---|
| netmiko >= 4.2.0 | SSH连接交换机，处理厂商差异 |
| paramiko >= 3.0.0 | SFTP文件传输 |
| pandas >= 2.0.0 | 数据处理 |
| openpyxl >= 3.1.0 | Excel读写 |
| pyyaml >= 6.0 | YAML模板解析 |
| tftpy >= 0.7.2 | TFTP传输（备选） |

## 输入Excel格式

参照 `h3c_hosts-9825-9131H02.xlsx` 的格式：

| 列名 | 说明 | 填写方 |
|---|---|---|
| Hostname | 设备主机名 | 人工 |
| Mgmt_IP | 管理口IP | 人工 |
| Vendor | 厂商（h3c / huawei / ruijie） | 人工 |
| patch_now | 当前补丁版本 | **工具回写** |
| patch_new | 目标补丁版本 | **工具回写** |
| patch_file | 补丁文件名 | 人工 |
| patch1_md5_base | 本地文件MD5 | **工具回写** |
| patch1_md5_uploaded | 设备端文件MD5 | **工具回写** |
| login_mode | 登录状态（OK/FAIL/UNREACHABLE） | **工具回写** |
| upload_success | 上传状态（OK/FAIL） | **工具回写** |
| update_result | 升级结果（SUCCESS/PARTIAL/FAIL-xxx） | **工具回写** |

人工只需填写前6列，后5列由工具在执行过程中逐步回写。675台设备执行中可随时打开Excel查看进度。

## 厂商命令模板

命令模板在 `vendor_templates/` 目录下，YAML格式，可直接编辑适配实际环境。

模板中的占位符：

- `{patch_file}` — 替换为Excel中的补丁文件名
- `{patch_id}` — 替换为补丁标识

### H3C模板（h3c.yaml）

```yaml
pre_check:
  - command: display patch information   # 检查当前补丁状态
  - command: display cpu-usage           # CPU使用率
  - command: display memory              # 内存使用率
  - command: display device              # 设备部件状态

activate:
  - command: "install activate patch flash:/{patch_file}"   # 激活补丁
  - command: install commit                                  # 确认补丁

rollback:
  - command: "install deactivate patch flash:/{patch_file}"  # 去激活补丁
  - command: install commit                                   # 确认去激活
  - command: "delete flash:/{patch_file}"                      # 删除补丁文件(可选)
```

### 华为模板（huawei.yaml）

```yaml
pre_check:
  - command: display patch information
  - command: display cpu-usage
  - command: display memory-usage
  - command: display device

activate:
  - command: "patch install {patch_file} flash:/"     # 安装补丁
  - command: "patch activate {patch_id}"               # 激活补丁

rollback:
  - command: "patch deactivate {patch_id}"
  - command: "patch delete {patch_id}"
  - command: "delete flash:/{patch_file}"
```

### 锐捷模板（ruijie.yaml）

```yaml
pre_check:
  - command: show patch
  - command: show cpu
  - command: show memory
  - command: show inventory

activate:
  - command: "patch install flash:/{patch_file}"   # 安装并激活

rollback:
  - command: "patch remove {patch_id}"
  - command: "delete flash:/{patch_file}"
```

如需调整命令，直接编辑对应YAML文件即可，无需修改代码。

## 使用方式

### 查看可用Sheet

```bash
python -m switch_patcher h3c_hosts.xlsx --list-sheets
```

### 预检查模式（Dry-run）

仅连接设备做健康检查和MD5计算，不传输文件、不激活补丁：

```bash
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --dry-run
```

### 指定Sheet灰度执行

```bash
python -m switch_patcher h3c_hosts.xlsx --sheet batch1 --username admin --password xxx
```

可在Excel中创建多个Sheet（如batch1、batch2、batch3），按批次灰度推进。

### 完整执行

```bash
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --workers 10
```

### 跳过已上传设备的文件传输

第一次执行后文件已上传，第二次只重试激活：

```bash
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --skip-uploaded
```

### 激活后自动保存配置

默认不save，确认补丁无问题后再手动保存。加`--save`自动保存：

```bash
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --save
```

### 调整健康检查阈值

CPU或内存超过80%就跳过：

```bash
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --cpu-threshold 80 --mem-threshold 80
```

## 全部参数

```
python -m switch_patcher <input.xlsx> [options]

位置参数:
  input                  输入Excel文件路径

选项:
  --sheet SHEET          指定要处理的Excel Sheet名称（灰度补丁）
  --list-sheets          列出Excel中所有可用Sheet后退出
  --dry-run              仅预检查，不传输文件、不激活补丁
  --workers N            最大并发设备连接数（默认: 5）
  --timeout N            单条命令SSH超时秒数（默认: 30）
  --save                 激活后自动保存配置（默认: 不保存）
  --cpu-threshold %      CPU使用率跳过阈值（默认: 90）
  --mem-threshold %      内存使用率跳过阈值（默认: 90）
  --transfer sftp|tftp   文件传输方式（默认: sftp）
  --username USER        SSH用户名（所有设备统一）
  --password PASS        SSH密码（所有设备统一）
  --ssh-port PORT        SSH端口（默认: 22）
  --patches-dir DIR      补丁文件存放目录（默认: patches）
  --skip-uploaded        跳过upload_success=OK的设备
```

## 执行流程

单设备完整流程分为6个阶段：

```
阶段0: 本地校验
  │  计算本地补丁文件MD5
  │  文件不存在则跳过
  ▼
阶段1: 预检查 (Pre-check)
  │  TCP连通性探测
  │  SSH登录（3次重试，间隔3秒）
  │  执行 display patch / cpu / memory / device
  │  提取当前补丁版本 → 写入 patch_now
  │  检查CPU/内存是否超阈值
  ▼
阶段2: 文件传输 (Transfer)
  │  SFTP上传补丁文件到 flash:/
  │  设备端执行 md5sum 校验文件完整性
  │  MD5匹配 → upload_success=OK
  │  不匹配 → upload_success=FAIL，不进入激活
  ▼
阶段3: 激活补丁 (Activate)
  │  进入config模式
  │  逐条执行activate命令，检查error_patterns
  │  默认不save（需 --save 才保存）
  ▼
阶段4: 后检查 (Post-check)
  │  再次执行 display patch / cpu / memory
  │  对比前后CPU/内存变化
  │  验证补丁已激活
  ▼
阶段5: 生成回退文件
     写入 rollback/{hostname}_{run_id}.txt
     只包含成功步骤的反序undo命令
```

每个阶段完成后立即回写Excel对应列。675台设备执行过程中可随时打开Excel查看进度。

## 断点续跑

工具天然支持断点续跑：

- `update_result=SUCCESS` 的设备 → 自动跳过
- `upload_success=OK` + `--skip-uploaded` → 跳过文件传输，直接激活
- 登录失败的设备 → 重新尝试3次登录

典型操作流程：

```bash
# 第一轮：全量执行
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx

# 检查结果，发现有30台登录失败
# 第二轮：只重试失败的（已成功的自动跳过）
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --skip-uploaded
```

## 输出文件

执行完成后生成以下文件：

```
项目目录/
├── logs/
│   ├── BJ-YZ-DC-SROCE_AGG-03_01_01_01.BJ_20260526_143000.log    # 设备1日志
│   ├── BJ-YZ-DC-SROCE_AGG-03_01_01_02.BJ_20260526_143000.log    # 设备2日志
│   └── ...
├── rollback/
│   ├── BJ-YZ-DC-SROCE_AGG-03_01_01_01.BJ_20260526_143000.txt    # 设备1回退命令
│   └── ...
└── h3c_hosts.xlsx                                                 # Excel已回写状态
```

回退文件示例：

```
# Rollback commands for: BJ-YZ-DC-SROCE_AGG-03_01_01_01.BJ (172.30.38.96)
# Vendor: h3c
# Generated: 2026-05-26 14:35:22
# Patch file: S9855_9825-CMW910-SYSTEM-R9131HS02.bin
# Run ID: 20260526_143000
# WARNING: Review before executing. Apply in the order listed.

install commit
install deactivate patch flash:/S9855_9825-CMW910-SYSTEM-R9131HS02.bin
```

## 控制台输出示例

```
=== Patch run 20260526_143000 started ===
Total devices: 675, Workers: 5, Dry-run: False
[1/675] BJ-YZ-DC-SROCE_AGG-03_01_01_01.BJ ... SUCCESS
[2/675] BJ-YZ-DC-SROCE_AGG-03_01_01_02.BJ ... SUCCESS
[3/675] BJ-YZ-DC-SROCE_AGG-03_01_01_03.BJ ... FAIL-LOGIN
...
=== Run 20260526_143000 complete ===
  Success: 640  Partial: 2  Failed: 18  Skipped: 15
  Failed login devices (18): BJ-YZ-DC-SROCE_AGG-..., ...

======================================================================
  Patch Summary - 2026-05-26 15:12:33
======================================================================
  Total: 675  |  Success: 640  |  Partial: 2  |  Failed: 18  |  Skipped: 15
----------------------------------------------------------------------
  [OK]       BJ-YZ-DC-SROCE_AGG-03_01_01_01.BJ           (172.30.38.96, h3c) - 2/2 commands applied
  [FAIL]     BJ-YZ-DC-SROCE_AGG-03_01_01_03.BJ           (172.30.38.98, h3c) - SSH login failed after 3 attempts
  [SKIP]     BJ-YZ-DC-SROCE_AGG-03_01_01_04.BJ           (172.30.38.99, h3c) - CPU 92% > threshold 90%
======================================================================
```

## 注意事项

1. **补丁文件放置** — 将所有补丁文件放入 `patches/` 目录，文件名须与Excel中的 `patch_file` 列一致
2. **SFTP前提** — H3C设备需提前启用SFTP：`sftp server enable`；华为默认支持
3. **MD5校验** — H3C使用 `md5sum` 命令校验；华为通过 `dir` 检查文件大小；锐捷使用 `verify` 命令
4. **保存配置** — 默认不save，给人工验证留窗口期。确认无误后手动save或加 `--save`
5. **回退操作** — 工具生成回退命令文件，需人工审核后在设备上执行
6. **并发控制** — `--workers` 控制并发数，建议生产环境不超过10，避免对设备管理口造成压力
7. **密码安全** — 密码通过命令行参数传递，建议执行后清理命令历史
