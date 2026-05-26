# Switch Patcher

批量给多厂商交换机打系统补丁的Python工具。支持H3C（Comware）、华为（VRP）、锐捷三大厂商，通过SSH连接设备，SFTP传输补丁文件，自动完成补丁激活前后健康检查，生成回退命令文件。

## 功能特性

- **多厂商支持** — H3C / 华为 / 锐捷，命令流程通过YAML模板定义，可直接编辑适配
- **Excel驱动** — 设备列表和执行进度都在同一个Excel中，工具实时回写状态列
- **灰度补丁** — 支持选择Excel中的不同Sheet，分批次执行
- **文件传输** — SFTP上传补丁文件，上传后MD5校验确保完整性
- **健康检查** — 打补丁前后检查CPU/内存/设备状态，超阈值自动跳过
- **登录重试** — TCP连通性前置检查 + SSH登录失败3次重试（间隔3秒）
- **断点续跑** — 重复执行自动跳过已成功的设备，只重试失败的
- **并发执行** — ThreadPoolExecutor设备间并发，进度实时显示
- **回退文件** — 为每台设备生成反序undo命令文件，人工确认后手动执行
- **默认不保存** — 补丁激活后默认不save，需`--save`显式开启
- **中文注释** — 所有源码均含完整中文注释，便于二次开发

## 项目结构

```
switch-patcher/
├── switch_patcher/              # 主程序包
│   ├── __init__.py               # 版本号
│   ├── __main__.py               # python -m switch_patcher 入口
│   ├── cli.py                    # 命令行参数定义
│   ├── excel_io.py               # Excel读写 + MD5计算 + 线程安全回写
│   ├── vendor_profiles.py        # YAML厂商模板加载 + 占位符替换
│   ├── connection.py             # netmiko SSH连接 + 重试逻辑
│   ├── file_transfer.py          # SFTP上传 + TCP连通性检查 + 设备端文件校验
│   ├── health_check.py           # CPU/内存/补丁版本正则解析 + 阈值判断
│   ├── device_worker.py          # 单设备6阶段完整流程编排
│   ├── batch_engine.py           # ThreadPoolExecutor并发调度 + 进度追踪
│   ├── reporting.py              # 执行汇总报告 + 失败设备清单
│   └── logger.py                 # per-device日志文件 + 控制台输出
├── vendor_templates/             # 可编辑的YAML命令模板
│   ├── h3c.yaml                  # H3C Comware补丁命令流程
│   ├── huawei.yaml                # 华为VRP补丁命令流程
│   └── ruijie.yaml               # 锐捷补丁命令流程
├── patches/                      # 补丁文件存放目录（使用前将补丁文件放入此处）
├── requirements.txt              # Python依赖清单
└── .gitignore
```

运行时自动创建以下目录：

| 目录 | 用途 |
|---|---|
| `logs/` | 每台设备的独立操作日志，格式：`{hostname}_{run_id}.log` |
| `rollback/` | 每台设备的回退命令文件，格式：`{hostname}_{run_id}.txt` |
| `backups/` | 设备配置备份（预留） |

## 环境准备

### 1. Python版本

需要 Python 3.12+。确认版本：

```bash
python --version
```

### 2. 克隆项目

```bash
git clone https://github.com/suhanlei/switch-patcher.git
cd switch-patcher
```

### 3. 创建虚拟环境

```bash
python -m venv venv
```

### 4. 激活虚拟环境

Windows CMD：

```cmd
venv\Scripts\activate
```

Windows PowerShell：

```powershell
venv\Scripts\Activate.ps1
```

Git Bash / MSYS2 / Linux / macOS：

```bash
source venv/Scripts/activate   # Windows Git Bash
source venv/bin/activate       # Linux/macOS
```

激活后命令行提示符会显示 `(venv)` 前缀，表示虚拟环境已激活。

### 5. 安装依赖

```bash
pip install -r requirements.txt
```

依赖列表：

| 包 | 版本 | 用途 |
|---|---|---|
| netmiko | >= 4.2.0 | SSH连接交换机，自动处理厂商CLI差异 |
| paramiko | >= 3.0.0 | SFTP文件传输通道 |
| pandas | >= 2.0.0 | 数据处理 |
| openpyxl | >= 3.1.0 | Excel文件读写 |
| pyyaml | >= 6.0 | YAML厂商模板解析 |
| tftpy | >= 0.7.2 | TFTP传输（备选方式） |

### 6. 放置补丁文件

将补丁文件放入 `patches/` 目录，文件名须与Excel中 `patch_file` 列一致：

```
patches/
├── S9855_9825-CMW910-SYSTEM-R9131HS02.bin
├── CE6800-V200R019SPH023.pat
└── RGOS-2.0-patch1.pat
```

## 输入Excel格式

参照 `h3c_hosts-9825-9131H02.xlsx` 的格式，Sheet中包含以下列：

| 列名 | 说明 | 填写方 |
|---|---|---|
| Hostname | 设备主机名 | 人工填写 |
| Mgmt_IP | 管理口IP | 人工填写 |
| Vendor | 厂商（h3c / huawei / ruijie） | 人工填写 |
| patch_now | 当前补丁版本 | **工具回写** |
| patch_new | 目标补丁版本 | **工具回写** |
| patch_file | 补丁文件名 | 人工填写 |
| patch1_md5_base | 本地补丁文件MD5 | **工具回写** |
| patch1_md5_uploaded | 设备端文件MD5 | **工具回写** |
| login_mode | 登录状态（OK/FAIL/UNREACHABLE） | **工具回写** |
| upload_success | 上传状态（OK/FAIL） | **工具回写** |
| update_result | 升级结果（SUCCESS/PARTIAL/FAIL-xxx） | **工具回写** |

人工只需填写前6列（Hostname ~ patch_file），后5列由工具在执行过程中逐步回写。

**灰度分批**：在Excel中创建多个Sheet（如 `batch1`、`batch2`、`batch3`），每批放入部分设备行，执行时通过 `--sheet` 参数指定批次。

## 厂商命令模板

命令模板在 `vendor_templates/` 目录下，YAML格式，可直接编辑适配实际环境，无需修改代码。

模板中的占位符：

- `{patch_file}` — 替换为Excel中的补丁文件名（如 `S9855_9825-CMW910-SYSTEM-R9131HS02.bin`）
- `{patch_id}` — 替换为补丁标识编号

### H3C模板（h3c.yaml）

适用于 H3C S9800/S9855/S9825 等Comware V9/V7交换机：

```yaml
pre_check:
  - command: display patch information   # 检查当前补丁状态
  - command: display cpu-usage           # CPU使用率
  - command: display memory              # 内存使用率
  - command: display device              # 设备部件状态

activate:
  - command: "install activate patch flash:/{patch_file}"   # 激活补丁
  - command: install commit                                  # 确认补丁生效

post_check:
  - command: display patch information   # 验证补丁已激活
  - command: display cpu-usage           # 检查补丁后CPU
  - command: display memory              # 检查补丁后内存

rollback:
  - command: "install deactivate patch flash:/{patch_file}"  # 去激活补丁
  - command: install commit                                   # 确认去激活
  - command: "delete flash:/{patch_file}"                      # 删除补丁文件(可选)

save: save force
md5_command: "md5sum flash:/{patch_file}"
```

### 华为模板（huawei.yaml）

适用于华为 CE6800/CE12800 等VRP交换机：

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

save: save
md5_command: "dir flash:/{patch_file}"     # 华为通过dir查看文件大小间接验证
```

### 锐捷模板（ruijie.yaml）

适用于锐捷 S6520/S6510 等交换机：

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

save: write
md5_command: "verify flash:/{patch_file}"
```

如需调整命令，直接编辑对应YAML文件即可，无需修改Python代码。

## 使用方式

所有命令需在虚拟环境中执行（提示符显示 `(venv)`）。

### 查看可用Sheet

```bash
python -m switch_patcher h3c_hosts.xlsx --list-sheets
```

输出示例：
```
Available sheets in h3c_hosts.xlsx:
  - all
  - batch1
  - batch2
```

### 预检查模式（Dry-run）

仅连接设备做健康检查和本地MD5计算，**不传输文件、不激活补丁**：

```bash
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --dry-run
```

### 指定Sheet灰度执行

```bash
# 先执行batch1（如50台设备）
python -m switch_patcher h3c_hosts.xlsx --sheet batch1 --username admin --password xxx

# 确认无问题后执行batch2
python -m switch_patcher h3c_hosts.xlsx --sheet batch2 --username admin --password xxx
```

### 完整执行

```bash
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --workers 10
```

### 跳过已上传设备的文件传输

第一次执行后文件已上传成功，第二次只重试激活阶段：

```bash
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --skip-uploaded
```

### 激活后自动保存配置

默认不save，确认补丁无问题后再手动保存。加 `--save` 自动保存：

```bash
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --save
```

### 调整健康检查阈值

CPU或内存超过80%就跳过该设备：

```bash
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --cpu-threshold 80 --mem-threshold 80
```

## 全部参数

```
python -m switch_patcher <input.xlsx> [options]

位置参数:
  input                       输入Excel文件路径

灰度与预检:
  --sheet SHEET               指定要处理的Excel Sheet名称（灰度分批）
  --list-sheets               列出Excel中所有可用Sheet后退出
  --dry-run                   仅预检查，不传输文件、不激活补丁

执行控制:
  --workers N                 最大并发设备连接数（默认: 5）
  --timeout N                 单条命令SSH超时秒数（默认: 30）
  --save                      激活后自动保存配置（默认: 不保存）
  --skip-uploaded             跳过upload_success=OK的设备

健康检查:
  --cpu-threshold %           CPU使用率跳过阈值（默认: 90）
  --mem-threshold %           内存使用率跳过阈值（默认: 90）

文件传输:
  --transfer sftp|tftp        文件传输方式（默认: sftp）
  --patches-dir DIR           补丁文件存放目录（默认: patches）

SSH认证:
  --username USER             SSH用户名（所有设备统一）
  --password PASS             SSH密码（所有设备统一）
  --ssh-port PORT             SSH端口（默认: 22）
```

## 执行流程

单设备完整流程分为6个阶段：

```
阶段0: 本地校验
  │  检查补丁文件是否存在于 patches/ 目录
  │  计算本地文件MD5 → 回写 patch1_md5_base
  │  文件不存在则标记 FAIL-NOFILE 并跳过
  ▼
阶段1: 连通性检查 + 预检查
  │  TCP端口探测（22端口可达性）
  │  SSH登录（3次重试，每次间隔3秒）
  │  登录成功 → 回写 login_mode=OK
  │  登录失败 → 回写 login_mode=FAIL, update_result=FAIL-LOGIN
  │  执行 display patch / cpu / memory / device
  │  提取当前补丁版本 → 回写 patch_now
  │  写入目标补丁版本 → 回写 patch_new
  │  CPU/内存超过阈值 → 标记 SKIP 并跳过
  ▼
阶段2: 文件传输
  │  若 upload_success=OK 则跳过（支持 --skip-uploaded）
  │  SFTP上传补丁文件到 flash:/
  │  上传失败 → 回写 upload_success=FAIL, update_result=FAIL-UPLOAD
  │  重连设备执行 md5sum 校验文件完整性
  │  MD5匹配 → 回写 upload_success=OK, patch1_md5_uploaded
  │  MD5不匹配 → 回写 upload_success=FAIL-MD5, 不进入激活阶段
  ▼
阶段3: 激活补丁（--dry-run 到此结束）
  │  进入config模式（netmiko自动处理厂商差异）
  │  逐条执行activate命令，每条检查error_patterns
  │  命令失败则记录但继续下一条（尽力而为）
  │  默认不save（需 --save 才保存）
  ▼
阶段4: 后检查
  │  重新SSH连接
  │  执行 display patch / cpu / memory
  │  对比补丁前后CPU/内存变化
  │  验证补丁版本已更新 → patch_applied=True/False
  ▼
阶段5: 生成回退文件
     写入 rollback/{hostname}_{run_id}.txt
     只包含成功执行步骤的反序undo命令
     文件头含设备信息、时间戳、补丁编号、操作警告
```

**关键设计**：
- 每个阶段独立建立和断开SSH连接，避免长连接超时断开
- 每个阶段完成后立即回写Excel，675台设备执行中可随时打开Excel查看进度
- Excel回写使用线程锁（threading.Lock），保证并发安全

## 断点续跑

工具天然支持断点续跑，基于Excel中的状态列判断：

| 上次状态 | 本次行为 |
|---|---|
| `update_result=SUCCESS` | 自动跳过，不重复执行 |
| `upload_success=OK` + `--skip-uploaded` | 跳过文件传输，直接进入激活阶段 |
| `login_mode=FAIL` | 重新尝试3次SSH登录 |
| `update_result=FAIL-xxx` | 从头重新执行完整流程 |

典型操作流程：

```bash
# 第一轮：全量执行
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx

# 检查结果，发现30台登录失败、5台上传失败
# 第二轮：只重试失败的（已成功的自动跳过）
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --skip-uploaded

# 检查结果，剩余5台仍然登录失败（可能设备离线）
# 第三轮：继续重试
python -m switch_patcher h3c_hosts.xlsx --username admin --password xxx --skip-uploaded
```

## 输出文件

执行完成后在Excel同目录下生成以下文件：

```
Excel同目录/
├── logs/
│   ├── BJ-YZ-DC-SROCE_AGG-03_01_01_01.BJ_20260526_143000.log
│   ├── BJ-YZ-DC-SROCE_AGG-03_01_01_02.BJ_20260526_143000.log
│   └── ...（每台设备一个日志文件）
├── rollback/
│   ├── BJ-YZ-DC-SROCE_AGG-03_01_01_01.BJ_20260526_143000.txt
│   └── ...（每台设备一个回退命令文件）
└── h3c_hosts.xlsx     （已回写所有状态列）
```

**设备日志文件** — 记录该设备从阶段0到阶段5的每一步操作和输出，用于审计和排障。

**回退文件示例**：

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

> 回退命令按**反序排列**：最后执行的激活步骤对应的undo排在最前。只包含成功执行的步骤，失败步骤的undo不会出现。

## 控制台输出示例

```
=== Patch run 20260526_143000 started ===
Total devices: 675, Workers: 5, Dry-run: False
[1/675] BJ-YZ-DC-SROCE_AGG-03_01_01_01.BJ ... SUCCESS
[2/675] BJ-YZ-DC-SROCE_AGG-03_01_01_02.BJ ... SUCCESS
[3/675] BJ-YZ-DC-SROCE_AGG-03_01_01_03.BJ ... FAIL-LOGIN
[4/675] BJ-YZ-DC-SROCE_AGG-03_01_01_04.BJ ... SKIP-CPU 92% > threshold 90%
...
=== Run 20260526_143000 complete ===
  Success: 640  Partial: 2  Failed: 18  Skipped: 15
  Failed login devices (18): BJ-YZ-DC-SROCE_AGG-..., ...

======================================================================
  Patch Summary - 2026-05-26 15:12:33
======================================================================
  Total: 675  |  Success: 640  |  Partial: 2  |  Failed: 18  |  Skipped: 15
----------------------------------------------------------------------
  [OK]       BJ-YZ-DC-SROCE_AGG-03_01_01_01.BJ   (172.30.38.96, h3c) - 2/2 commands applied
  [PARTIAL]  BJ-YZ-DC-SROCE_AGG-03_01_01_05.BJ   (172.30.38.101, h3c) - 1/2 commands applied
  [FAIL]     BJ-YZ-DC-SROCE_AGG-03_01_01_03.BJ   (172.30.38.98, h3c) - SSH login failed after 3 attempts
  [SKIP]     BJ-YZ-DC-SROCE_AGG-03_01_01_04.BJ   (172.30.38.99, h3c) - CPU 92% > threshold 90%
======================================================================

  Failed login devices (re-run will retry these):
    - BJ-YZ-DC-SROCE_AGG-03_01_01_03.BJ (172.30.38.98)
    - BJ-YZ-DC-SROCE_AGG-03_01_01_06.BJ (172.30.38.102)
    ...
```

## 注意事项

1. **补丁文件放置** — 将所有补丁文件放入 `patches/` 目录，文件名须与Excel中的 `patch_file` 列完全一致
2. **SFTP前提** — H3C设备需提前启用SFTP服务：`sftp server enable`；华为设备默认支持
3. **MD5校验方式** — H3C使用 `md5sum` 命令；华为通过 `dir` 查看文件大小间接验证；锐捷使用 `verify` 命令
4. **保存配置** — 默认不save，给人工验证留窗口期。确认补丁生效后手动save或加 `--save` 重新执行
5. **回退操作** — 工具只生成回退命令文件，不自动执行。需人工审核后登录设备手动执行
6. **并发控制** — `--workers` 控制并发数，建议生产环境不超过10，避免对设备管理口造成压力
7. **密码安全** — 密码通过命令行参数传递，建议执行后清理Shell历史记录
8. **二次开发** — 所有源码均有完整中文注释，YAML模板可直接编辑，方便适配其他厂商或自定义流程
