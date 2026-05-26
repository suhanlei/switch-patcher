# Switch Patcher

批量给多厂商交换机打系统补丁的Python工具。支持H3C（Comware）、华为（VRP）、锐捷三大厂商，通过SSH连接设备，SFTP传输补丁文件，自动完成补丁激活前后健康检查，生成回退命令文件。

## 功能特性

- **多厂商支持** — H3C / 华为 / 锐捷，命令流程通过YAML模板定义，可直接编辑适配
- **一条命令执行** — 编辑 `patch_config.yaml` 后，`python -m switch_patcher` 一键完成全流程
- **Excel驱动** — 设备列表和执行进度都在同一个Excel中，工具实时回写状态列
- **灰度补丁** — 支持选择Excel中的不同Sheet，分批次执行
- **6步自动编排** — check_scp → open_scp → recheck_scp → upload → activate → finalize
- **Excel字段驱动跳过** — 基于 scp_status、upload_success、update_result 字段自动跳过已完成步骤，无需手动传参
- **文件传输** — SFTP上传补丁文件，上传后MD5/文件大小校验确保完整性
- **健康检查** — 打补丁前后检查补丁状态和设备信息，H3C自动检测补丁已激活/不兼容
- **交互式命令** — 自动处理补丁激活时的Y/N确认提示（H3C install activate、华为 patch load）
- **锐捷3步流程** — upgrade → active → running，每步等待进度100%完成
- **H3C补丁检测** — 自动检测"已激活"和"不兼容"错误，避免重复操作
- **SCP/SFTP状态检查** — 先检查设备是否已开启SCP/SFTP，已开启的自动跳过
- **登录重试** — SSH登录失败3次重试（间隔3秒），TCP连通性前置检查
- **锐捷限速** — 连接锐捷设备前自动等待1秒，避免过快连接被拒绝
- **H3C大缓冲** — H3C设备使用40MB接收缓冲区，适应display命令大量输出
- **死循环保护** — 所有while循环最多60次迭代后强制退出
- **断点续跑** — 重复执行自动跳过已成功的设备，只重试失败的
- **并发执行** — ThreadPoolExecutor设备间并发，进度实时显示
- **回退文件** — 为每台设备生成反序undo命令文件，人工确认后手动执行
- **默认不保存** — 补丁激活后默认不save，需在配置文件或CLI中显式开启
- **中文注释** — 所有源码均含完整中文注释，便于二次开发

## 项目结构

```
switch-patcher/
├── switch_patcher/              # 主程序包
│   ├── __init__.py               # 版本号
│   ├── __main__.py               # python -m switch_patcher 入口
│   ├── cli.py                    # 命令行参数解析，配置文件合并
│   ├── config.py                 # patch_config.yaml 加载与生成
│   ├── excel_io.py               # Excel读写 + MD5计算 + 线程安全回写
│   ├── vendor_profiles.py         # YAML厂商模板加载 + 占位符替换
│   ├── connection.py              # netmiko SSH连接（单次尝试，重试由上层控制）
│   ├── file_transfer.py          # SFTP上传 + TCP连通性检查 + 设备端文件校验
│   ├── health_check.py           # 补丁版本解析 + H3C错误检测 + SCP状态检查
│   ├── device_worker.py          # 单设备各步骤函数（check_scp/enable_scp/upload/activate等）
│   ├── batch_engine.py           # 6步编排调度 + ThreadPoolExecutor并发 + 进度追踪
│   ├── reporting.py              # 执行汇总报告 + 失败设备清单
│   └── logger.py                 # per-device日志文件 + 控制台输出
├── vendor_templates/             # 可编辑的YAML命令模板
│   ├── h3c.yaml                  # H3C Comware补丁命令流程
│   ├── huawei.yaml                # 华为VRP补丁命令流程
│   └── ruijie.yaml               # 锐捷补丁命令流程
├── patches/                      # 补丁文件存放目录（使用前将补丁文件放入此处）
├── patch_config.yaml             # 执行配置文件（Excel路径、Sheet、用户名密码等）
├── patch_hosts_template.xlsx      # Excel模板文件
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

Python 3.8+

### 2. 创建虚拟环境并安装依赖

```bash
# 创建虚拟环境（使用virtualenvwrapper）
mkvirtualenv /home/virtual_path/switch-patcher

# 激活虚拟环境
workon switch-patcher

# 安装依赖
cd /path/to/switch-patcher
pip install -r requirements.txt
```

### 3. 验证安装

```bash
python -m switch_patcher --help
```

### 4. PyCharm 远程开发配置

本地编辑代码，服务器上运行，PyCharm 做桥梁：

**配置 Deployment（SFTP 同步）：**

`Tools → Deployment → Configuration → + → SFTP`

```
SSH Host:       服务器IP
Port:           22
Username:       root

Mappings 标签页：
  Local Path:       本地项目目录
  Deployment Path:  /switch-patcher
```

开启自动上传：`Tools → Deployment → Options → Upload changed files: Always`

**配置远程解释器：**

`File → Settings → Project → Python Interpreter → ⚙ → Add → SSH Interpreter`

```
Interpreter:  /home/virtual_path/switch-patcher/bin/python3
```

配置完成后，kscc 或 PyCharm 修改代码 → 自动同步到服务器 → PyCharm Run 在服务器上执行。

## 输入Excel格式

参照 `patch_hosts_template.xlsx` 的格式，Sheet中包含以下列：

| 列名 | 说明 | 填写方 |
|---|---|---|
| Hostname | 设备主机名 | 人工填写 |
| Mgmt_IP | 管理口IP | 人工填写 |
| Vendor | 厂商（h3c / huawei / ruijie） | 人工填写 |
| patch_file | 补丁文件名 | 人工填写 |
| patch_now | 当前补丁版本 | **工具回写** |
| patch_new | 目标补丁版本 | **工具回写** |
| patch1_md5_base | 本地补丁文件MD5 | **工具回写** |
| patch1_md5_uploaded | 设备端文件MD5 | **工具回写** |
| scp_status | 设备连接与服务状态 | **工具回写** |
| upload_success | 上传状态（OK/FAIL） | **工具回写** |
| update_result | 升级结果（SUCCESS/PARTIAL/FAIL-xxx） | **工具回写** |

人工只需填写 Hostname、Mgmt_IP、Vendor、patch_file 四列，其余7列由工具在执行过程中逐步回写。

`scp_status` 合并了原 `login_mode` 字段，一个字段同时表达连接状态和服务状态：

| scp_status 值 | 含义 |
|---|---|
| `scp` / `sftp` / `scp_sftp` | SCP/SFTP已开启（隐含登录OK） |
| `none` | SCP/SFTP未开启（隐含登录OK，需要执行开启） |
| `unreachable` | 设备不可达（TCP探测失败） |
| `login_fail` | SSH登录失败（3次重试后仍失败） |
| 空 | 未检查，需要执行步骤1 |

**灰度分批**：在Excel中创建多个Sheet（如 `batch1`、`batch2`、`batch3`），每批放入部分设备行，执行时通过 `--sheet` 参数指定批次，或在 `patch_config.yaml` 中配置 `sheet` 字段。

## 厂商命令模板

命令模板在 `vendor_templates/` 目录下，YAML格式，可直接编辑适配实际环境，无需修改代码。

模板中的占位符：

- `{patch_file}` — 替换为Excel中的补丁文件名（如 `S9855_9825-CMW910-SYSTEM-R9131HS02.bin`）
- `{patch_id}` — 替换为补丁标识编号

### H3C模板（h3c.yaml）

适用于 H3C S9800/S9855/S9825 等Comware V9/V7交换机：

```yaml
vendor: H3C
# H3C设备display输出量大，需要40MB大接收缓冲区
recv_buffer_size: 40960000

# SCP/SFTP状态检查命令（先确认设备是否已开启，再决定是否需要执行使能）
check_scp_commands:
  - command: "display cur | in scp"
    key: scp
  - command: "display cur | in sftp"
    key: sftp

# SCP/SFTP前置使能命令（仅对scp_status=none的设备执行）
scp_enable_commands:
  - command: system-view
    description: 进入系统视图
  - command: scp server enable
    description: 启用SCP服务
  - command: sftp server enable
    description: 启用SFTP服务

pre_check:
  - command: display patch information
    key: patch_info
  - command: display device
    key: device

activate:
  # H3C install activate命令会提示[Y/N]确认，需自动回答Y
  - command: "install activate patch flash:/{patch_file}"
    description: 激活补丁（交互式，需确认Y）
    expect_pattern: "[Yy]/[Nn]"
    auto_reply: "Y"
  - command: install commit
    description: 确认补丁

post_check:
  - command: display patch information
    key: patch_info

rollback:
  - command: "install deactivate patch flash:/{patch_file}"
    description: 去激活补丁
  - command: install commit
    description: 确认去激活
  - command: "delete flash:/{patch_file}"
    description: 删除补丁文件(可选)

save: save force
error_patterns:
  - "Error"
  - "Unrecognized command"
  - "Incomplete command"
  - "Ambiguous command"
  - "Wrong parameter"
  - "Failed to"
  - "No such file"
  - "cannot be activated again"   # 补丁已激活，不能重复激活
  - "not compliant"               # 补丁与设备不兼容
md5_command: "md5sum flash:/{patch_file}"
verify_method: md5
```

### 华为模板（huawei.yaml）

适用于华为 CE6800/CE12800 等VRP交换机：

```yaml
vendor: Huawei
recv_buffer_size: 409600

# SCP/SFTP状态检查命令
check_scp_commands:
  - command: "display cur | in scp"
    key: scp
  - command: "display cur | in sftp"
    key: sftp

# SCP/SFTP前置使能命令（仅对scp_status=none的设备执行）
scp_enable_commands:
  - command: system-view
    description: 进入系统视图
  - command: scp server enable
    description: 启用SCP服务
  - command: sftp server enable
    description: 启用SFTP服务
  - command: commit
    description: 提交配置（华为CE系列需要commit生效）

pre_check:
  - command: display patch information
    key: patch_info
  - command: display device
    key: device

activate:
  # 华为 patch load 一步完成安装并激活，all run表示对所有槽位立即生效
  # 命令会提示[Y/N]确认，需自动回答Y
  - command: "patch load {patch_file} all run"
    description: 加载并运行补丁（交互式，需确认Y）
    expect_pattern: "[Yy]/[Nn]"
    auto_reply: "Y"

post_check:
  - command: display patch information
    key: patch_info

rollback:
  - command: "patch deactivate {patch_id}"
    description: 去激活补丁
  - command: "patch delete {patch_id}"
    description: 删除补丁
  - command: "delete flash:/{patch_file}"
    description: 删除补丁文件

save: save
# 华为不支持md5sum命令，通过dir查看文件大小间接验证完整性
md5_command: "dir flash:/{patch_file}"
verify_method: size
```

### 锐捷模板（ruijie.yaml）

适用于锐捷 S6520/S6510 等交换机：

```yaml
vendor: Ruijie
# 锐捷设备连接前需等待1秒（限速保护）
connect_delay: 1
recv_buffer_size: 409600

# SCP/SFTP状态检查命令
check_scp_commands:
  - command: "show run | in scp server"
    key: scp
  - command: "show run | in sftp server"
    key: sftp

# SCP/SFTP前置使能命令（仅对scp_status=none的设备执行）
scp_enable_commands:
  - command: configure terminal
    description: 进入配置模式
  - command: ip scp server enable
    description: 启用SCP服务

pre_check:
  - command: show patch
    key: patch_info
  - command: show inventory
    key: device

# 锐捷补丁需3步操作，每步都需等待100%完成：
# 1. upgrade flash:{patch} — 安装补丁包，等待进度100%
# 2. patch active — 激活补丁，等待进度100%
# 3. patch running — 使补丁运行生效，等待进度100%
activate:
  - command: "upgrade flash:/{patch_file}"
    description: 安装补丁（等待100%完成）
    wait_progress: true
    progress_pattern: "\\d+%"
    complete_pattern: "100%"
  - command: patch active
    description: 激活补丁（等待100%完成）
    wait_progress: true
    progress_pattern: "\\d+%"
    complete_pattern: "100%"
  - command: patch running
    description: 使补丁运行生效（等待100%完成）
    wait_progress: true
    progress_pattern: "\\d+%"
    complete_pattern: "100%"

post_check:
  - command: show patch
    key: patch_info

rollback:
  # 锐捷回退：patch delete删除补丁，也需等待100%完成
  - command: patch delete
    description: 删除补丁（等待100%完成）
    wait_progress: true
    progress_pattern: "\\d+%"
    complete_pattern: "100%"
  - command: "delete flash:/{patch_file}"
    description: 删除补丁文件

save: write
patch_id_pattern: "Patch name:\\s*(\\S+)"
error_patterns:
  - "% Invalid input"
  - "% Incomplete command"
  - "% Ambiguous command"
  - "% Error"
md5_command: "verify flash:/{patch_file}"
verify_method: md5
```

如需调整命令，直接编辑对应YAML文件即可，无需修改Python代码。

## 使用方式

所有命令需在虚拟环境中执行（提示符显示 `(venv)`）。

### 快速开始

```bash
# 1. 生成示例配置文件
python -m switch_patcher --gen-config

# 2. 编辑配置文件，填入Excel路径、Sheet名、用户名密码
#    vim patch_config.yaml

# 3. 一条命令执行全流程
python -m switch_patcher
```

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
python -m switch_patcher --dry-run
```

### 指定Sheet灰度执行

配置文件中已填好用户名密码时，直接指定Sheet即可：

```bash
python -m switch_patcher --sheet batch1
```

也可临时覆盖Excel路径或凭据：

```bash
python -m switch_patcher h3c_hosts.xlsx --sheet batch1 --username admin --password xxx
```

### 覆盖配置文件参数

CLI参数优先级高于配置文件：

```bash
python -m switch_patcher --workers 10 --save
```

### 断点续跑

重复执行时，工具自动基于Excel字段跳过已完成步骤：

- `scp_status` 已有值（scp/sftp/scp_sftp） → 跳过SCP检查和开启
- `scp_status=unreachable` 或 `login_fail` → 跳过该设备（下次重试时如果恢复会重新检查）
- `upload_success=OK` → 跳过文件上传
- `update_result=SUCCESS` → 跳过补丁激活

无需手动传 `--skip-uploaded`，工具自动判断。

## 全部参数

```
python -m switch_patcher [input] [options]

位置参数:
  input                       输入Excel文件路径（也可在patch_config.yaml中配置）

常用选项:
  --sheet SHEET               指定Excel Sheet名称（灰度分批），覆盖配置文件
  --username USER             SSH用户名，覆盖配置文件
  --password PASS             SSH密码，覆盖配置文件
  --dry-run                   仅预检查，不传输文件、不激活补丁
  --list-sheets               列出Excel中所有可用Sheet后退出
  --gen-config                生成示例配置文件 patch_config.yaml

执行控制:
  --workers N                 最大并发设备连接数（默认: 5）
  --timeout N                 SSH超时秒数（默认: 30）
  --save                      激活后自动保存配置（默认: 不保存）
  --ssh-port PORT             SSH端口（默认: 22）
  --patches-dir DIR           补丁文件存放目录（默认: patches）

参数优先级: CLI参数 > patch_config.yaml > 默认值
```

## 执行流程

工具自动按6步顺序编排执行，每步基于Excel字段自动过滤设备：

```
步骤1: check_scp — 检查设备SCP/SFTP状态
  │  先TCP探测端口，不可达直接写 scp_status=unreachable
  │  SSH登录执行check_scp_commands（3次重试）
  │  检测输出中的 scp server enable / sftp server enable 关键字
  │  写入scp_status列: scp / sftp / scp_sftp / none / unreachable / login_fail
  │  已有scp_status的设备自动跳过
  ▼
步骤2: open_scp — 开启SCP/SFTP服务
  │  只对scp_status=none的设备执行
  │  执行scp_enable_commands（进入系统视图→开启SCP→开启SFTP）
  │  执行后保存配置（处理Y/N交互）
  ▼
步骤3: recheck_scp — 再次确认已开启
  │  对步骤2后仍为none的设备重新检查
  │  更新scp_status列
  ▼
步骤4: upload — 上传补丁文件
  │  只对upload_success≠OK且scp_status为scp/sftp/scp_sftp的设备执行
  │  本地文件校验 → SFTP上传 → 设备端MD5/大小校验
  │  校验通过 → 写upload_success=OK
  │  校验失败 → 写upload_success=FAIL，不进入激活
  ▼
步骤5: activate — 激活补丁（--dry-run到此结束）
  │  只对upload_success=OK且update_result≠SUCCESS且scp_status不为unreachable/login_fail的设备执行
  │  预检查：获取补丁版本 + H3C错误模式检测
  │  进入config模式，逐条执行activate命令
  │  交互式命令自动回复Y/N，锐捷3步等待进度100%
  │  每条检查error_patterns（含H3C"已激活"/"不兼容"）
  │  默认不save
  ▼
步骤6: post_check + rollback — 后检查与生成回退
  │  重新SSH连接执行post_check命令
  │  验证补丁版本已更新
  │  为成功激活的步骤生成反序undo命令文件
  ▼
汇总: 打印成功/失败/跳过统计 + 登录失败设备清单
```

**断点续跑**：重复执行时，每步开始前重新读取Excel，自动跳过已完成的设备。例如：
- 第一轮执行到上传阶段因网络中断 → 第二轮自动跳过SCP检查和开启，只重新上传和激活

**关键设计**：
- 每个阶段独立建立和断开SSH连接，避免长连接超时断开
- 每个阶段完成后立即回写Excel，数百台设备执行中可随时打开Excel查看进度
- Excel回写使用线程锁（threading.Lock），保证并发安全

## 断点续跑

工具天然支持断点续跑，基于Excel中的状态字段自动判断：

| Excel字段状态 | 工具行为 |
|---|---|
| `scp_status` 已有值（scp/sftp/scp_sftp） | 跳过SCP检查和开启步骤 |
| `scp_status=none` | 执行SCP/SFTP开启命令 |
| `scp_status=unreachable` | 跳过该设备（下次重试时如果恢复会重新检查） |
| `scp_status=login_fail` | 跳过该设备（下次重试时重新尝试3次登录） |
| `upload_success=OK` | 跳过文件上传步骤 |
| `upload_success=FAIL` | 重新上传补丁文件 |
| `update_result=SUCCESS` | 跳过补丁激活步骤 |
| `update_result=FAIL-xxx` | 重新执行完整流程 |

典型操作流程：

```bash
# 第一轮：全量执行
python -m switch_patcher

# 检查结果，发现30台登录失败、5台上传失败
# 第二轮：只重试失败的（已完成的自动跳过）
python -m switch_patcher

# 检查结果，剩余5台仍然登录失败（可能设备离线）
# 第三轮：继续重试
python -m switch_patcher
```

## 配置文件说明

`patch_config.yaml` 示例：

```yaml
# Switch Patcher 补丁执行配置文件
# 执行命令：python -m switch_patcher（自动读取此文件）
# CLI参数会覆盖此文件中的对应值

# === 必填项 ===
excel: ""                     # Excel文件路径（相对或绝对路径）
sheet: ""                     # 要处理的Sheet名称（灰度分批）
username: ""                  # SSH用户名（所有设备统一）
password: ""                  # SSH密码（所有设备统一）

# === 可选项 ===
ssh_port: 22                  # SSH端口
workers: 5                     # 最大并发设备连接数
timeout: 30                   # 单条命令SSH超时秒数
patches_dir: patches           # 补丁文件存放目录
save: false                    # 激活后是否自动保存配置
dry_run: false                # 预检查模式（只做健康检查，不传输不激活）
```

生成方式：

```bash
python -m switch_patcher --gen-config
```

## 注意事项

1. **配置文件** — 首次使用先执行 `python -m switch_patcher --gen-config`，编辑 `patch_config.yaml` 填入Excel路径、Sheet名和SSH凭据
2. **SCP/SFTP状态** — 工具自动检查并开启，已开启的设备跳过；状态记录在Excel `scp_status` 列，重复执行不会重复开启
3. **文件校验方式** — H3C使用 `md5sum` 命令精确校验MD5；华为通过 `dir` 查看文件大小间接验证；锐捷使用 `verify` 命令校验
4. **交互式命令** — H3C的 `install activate` 和华为的 `patch load` 会提示Y/N确认，工具自动回复Y；锐捷3步流程每步等待进度100%完成
5. **H3C大缓冲区** — H3C设备使用40MB接收缓冲区，适应display命令大量输出
6. **锐捷限速** — 连接锐捷设备前自动等待1秒，避免过快连接被设备拒绝
7. **死循环保护** — 所有while循环最多60次迭代后强制退出，防止设备输出异常导致无限等待
8. **保存配置** — 默认不save，给人工验证留窗口期。可在配置文件设置 `save: true` 或用 `--save` 覆盖
9. **回退操作** — 工具只生成回退命令文件，不自动执行。需人工审核后登录设备手动执行
10. **并发控制** — `workers` 控制并发数，建议生产环境不超过10，避免对设备管理口造成压力
11. **密码安全** — 密码可存于配置文件（建议设置文件权限600），也可通过CLI参数传递（执行后清理Shell历史）
12. **二次开发** — 所有源码均有完整中文注释，YAML模板可直接编辑，方便适配其他厂商或自定义流程
