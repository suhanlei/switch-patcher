# Switch Patcher

批量给多厂商交换机打系统补丁的Python工具。支持H3C（Comware V7/V9）、华为（VRP/CE）、锐捷三大厂商，通过SSH连接设备，SFTP传输补丁文件，自动完成补丁激活前后健康检查，生成回退命令文件。

## 功能特性

- **多厂商支持** — H3C（Comware V7/V9）/ 华为（VRP/CE） / 锐捷，命令流程通过YAML模板定义，可直接编辑适配
- **一条命令执行** — 编辑 `patch_config.yaml` 后，`python -m switch_patcher` 一键完成全流程
- **Excel驱动** — 设备列表和执行进度都在同一个Excel中，工具实时回写状态列
- **灰度补丁** — 支持选择Excel中的不同Sheet，分批次执行
- **6步自动编排** — check_scp → open_scp → recheck_scp → upload → activate → finalize
- **Excel字段驱动跳过** — 基于 scp_status、upload_success、update_result 字段自动跳过已完成步骤，无需手动传参
- **登录重试** — SSH登录失败3次重试（间隔3秒），TCP连通性前置检查
- **旧版SSH兼容** — 遇到SSH算法协商失败自动用旧版算法（SHA1/ssh-rsa）重试，兼容旧版H3C/华为交换机
- **Y/N确认全局自动回复** — 所有命令执行中检测到[Y/N]提示自动回复Y，无需在YAML里逐条配置
- **H3C大缓冲区** — H3C设备设置paramiko Transport 40MB接收窗口，适应display命令大量输出
- **补丁进度等待** — install activate/commit等命令确认后持续等待直到操作完成，不会提前返回
- **断点续跑** — 中断后重新执行，已完成步骤自动跳过，从失败处继续
- **回退文件** — 每台设备生成独立回退命令文件，人工审核后手动执行

## 项目结构

```
switch-patcher/
├── switch_patcher/                  # 主程序包
│   ├── __init__.py
│   ├── __main__.py                  # 入口：python -m switch_patcher
│   ├── cli.py                       # 命令行接口（配置文件 + CLI参数覆盖）
│   ├── config.py                    # 配置文件加载/生成
│   ├── excel_io.py                  # Excel读写（线程安全回写）
│   ├── vendor_profiles.py           # 厂商YAML模板加载 + 命令占位符替换
│   ├── connection.py                # netmiko SSH连接（H3C大缓冲区 + 旧版算法兼容）
│   ├── file_transfer.py             # TCP探测、SFTP上传、设备端文件校验
│   ├── health_check.py              # 补丁版本解析、H3C错误检测、SCP状态检查
│   ├── device_worker.py             # 单设备步骤函数（统一命令发送 + Y/N自动回复）
│   ├── batch_engine.py              # 6步编排调度 + ThreadPoolExecutor并发
│   ├── reporting.py                  # 汇总报告
│   └── logger.py                    # per-device日志文件 + 控制台输出
├── vendor_templates/                # 厂商命令YAML模板
│   ├── h3c.yaml                     # H3C Comware V7/V9
│   ├── huawei.yaml                  # 华为 VRP/CE
│   └── ruijie.yaml                  # 锐捷
├── docs/                            # 开发沟通记录与流程文档
├── patches/                         # 补丁文件存放目录
├── packages/                        # 离线安装wheel包
├── patch_config.yaml                # 执行配置文件
├── requirements.txt                 # 依赖清单（含所有传递依赖）
└── README.md
```

## 环境准备

### 1. Python版本

Python 3.8+（当前依赖锁定为 Python 3.8 兼容版本）

### 2. 创建虚拟环境并安装依赖

#### 在线安装

```bash
mkvirtualenv /home/virtual_path/switch-patcher
workon switch-patcher
cd /home/virtual_path/switch-patcher
pip install -r requirements.txt
```

#### 离线安装（内网环境）

`requirements.txt` 包含所有直接依赖和传递依赖的精确版本，`packages/` 目录提供对应的 wheel 文件：

```bash
mkvirtualenv /home/virtual_path/switch-patcher
workon switch-patcher
cd /home/virtual_path/switch-patcher
pip install --no-index --find-links=packages/ -r requirements.txt
```

如需在有网机器上重新下载 wheel 包：

```bash
pip download -r requirements.txt -d packages/ \
    --platform manylinux2014_x86_64 \
    --python-version 38 \
    --only-binary=:all: \
    --implementation cp --abi cp38
```

> 注意：部分纯 Python 包（如 `paramiko`、`netmiko`、`scp` 等 `py3-none-any` 类型）不区分平台，可直接下载。

### 3. 关键依赖说明

| 包 | 锁定版本 | 说明 |
|---|---|---|
| netmiko | 4.2.0 | 主依赖（4.7.0+需Python≥3.10） |
| paramiko | 3.5.1 | SSH连接（5.0.0需Python≥3.9） |
| cryptography | 42.0.8 | SSL/TLS（46.0.0的OpenSSL 3.x移除了SHA1默认支持，导致旧版交换机SSH协商失败） |
| openpyxl | 3.1.5 | Excel读写 |
| PyYAML | 6.0.2 | YAML配置 |
| ntc-templates | 7.9.0 | netmiko解析模板（9.x需Python≥3.10） |

> **cryptography 版本必须锁定 42.0.8**：46.0.0 使用 OpenSSL 3.x，SHA1 密钥交换算法被移到 legacy provider 且默认不加载，导致部分旧版 H3C/华为交换机 SSH 握手失败（报 `SSHS_VERSION_MISMATCH`）。

## 快速开始

### 1. 生成配置文件

```bash
python -m switch_patcher --gen-config
```

### 2. 编辑配置文件

```bash
vi patch_config.yaml
```

```yaml
excel: "/home/virtual_path/switch-patcher/9827_template.xlsx"
sheet: "batch1"                   # Excel中的Sheet名称
username: "admin"                  # SSH用户名
password: "your_password"         # SSH密码
ssh_port: 22
workers: 5
timeout: 30
patches_dir: patches
save: false
dry_run: false
```

### 3. 查看可用Sheet

```bash
python -m switch_patcher --list-sheets
```

### 4. 执行补丁

```bash
# 正式执行
python -m switch_patcher

# 预检查模式（只做健康检查，不传输不激活）
python -m switch_patcher --dry-run
```

## 厂商补丁安装流程

### H3C（Comware V7/V9）

```
install activate patch flash:/<补丁文件> all   → 激活补丁（需确认Y，等待完成）
install commit                                   → 提交补丁（确保重启后生效，等待完成）
```

- `install activate` 立即生效，无需重启设备
- `install commit` 必须执行，否则重启后补丁丢失
- `all` 参数覆盖所有槽位，双主控设备无需分别指定 slot

**验证命令：**
```
display install active        → 查看当前已激活的补丁
display install committed     → 查看已提交的补丁
```

> 注意：Comware V7/V9 不存在 `display patch information` 命令（那是 V5 的命令）。

### 华为（VRP/CE）

```
patch load <补丁文件> all run   → 加载+激活+运行（一步完成，需确认Y）
```

- `all run` = 对所有单板加载并直接运行，合并 load→active→run 三步
- 补丁立即生效，无需重启设备

**验证命令：**
```
display patch-information      → 查看补丁状态（State应为Running）
```

### 锐捷

```
upgrade flash:/<补丁文件>       → 升级（等待进度100%）
patch active <补丁文件>         → 激活（等待进度100%）
patch running <补丁文件>        → 运行（等待进度100%）
```

- 3步流程，每步需等待进度达到100%

**验证命令：**
```
show version                   → 查看版本信息
```

## YAML模板配置

### config_mode_required

控制激活命令是否需要进入 config 模式：

| 厂商 | config_mode_required | 说明 |
|---|---|---|
| H3C | false | `install activate/commit` 在用户视图执行 |
| 华为 | false | `patch load` 在用户视图执行 |
| 锐捷 | false | `upgrade/patch active/patch running` 在用户视图执行 |

### wait_progress

控制命令是否需要持续等待直到操作完成：

```yaml
activate:
  - command: "install activate patch flash:/{patch_file} all"
    wait_progress: true      # 确认Y后持续等待直到设备提示符出现
  - command: install commit
    wait_progress: true      # commit需要几分钟，等待完成
```

### Y/N确认自动回复

**无需在YAML里配置**。工具全局自动检测 `[Y/N]:` 提示并回复 Y，适用于：
- `install activate` 的确认提示
- `install commit` 的确认提示
- `patch load` 的确认提示
- `save` 的确认提示
- 任何其他命令的 Y/N 交互

### error_patterns

命令执行错误的匹配模式，匹配到则判定命令失败：

```yaml
error_patterns:
  - "^Error:"              # 精确匹配行首Error（避免误匹配正常输出中的Error单词）
  - "Unrecognized command"
  - "Incomplete command"
  - "cannot be activated again"
```

> 注意：`error_patterns` 应尽量精确，避免过于宽泛的匹配（如 `"Error"` 或 `"Failed"`）导致正常输出被误判为错误。

## Excel格式要求

- **格式**：必须为 `.xlsx`（openpyxl不支持 `.xls`）
- **表头**：Hostname、Mgmt_IP、Vendor、patch_file 为必填列
- **Vendor值**：h3c / huawei / ruijie（不区分大小写）
- **状态列**：scp_status、upload_success、update_result 由工具自动回写

| 列名 | 说明 | 示例 |
|---|---|---|
| Hostname | 设备主机名 | QY-TEST-AZ-ROCE_TOR-01 |
| Mgmt_IP | 管理口IP | 172.30.240.159 |
| Vendor | 厂商 | h3c |
| patch_file | 补丁文件名 | S9857_9827-CMW910-SYSTEM-R9316HS04.bin |
| patch_now | 当前补丁版本（工具回写） | S9857_9827-CMW910-SYSTEM-R9316HS01.bin |
| patch_new | 目标补丁版本（工具回写） | S9857_9827-CMW910-SYSTEM-R9316HS04.bin |
| scp_status | SCP/SFTP状态（工具回写） | scp_sftp / none / unreachable / login_fail |
| upload_success | 上传状态（工具回写） | OK / FAIL / FAIL-MD5 |
| update_result | 最终结果（工具回写） | SUCCESS / FAIL-* / SKIP-* / DRYRUN-OK |

## 注意事项

1. **配置文件** — 首次使用先执行 `python -m switch_patcher --gen-config`，编辑 `patch_config.yaml` 填入Excel路径、Sheet名和SSH凭据
2. **Excel格式** — 必须为 `.xlsx` 格式（openpyxl 不支持 `.xls`），Sheet表头需包含 Hostname、Mgmt_IP、Vendor、patch_file 等列
3. **离线安装** — 内网环境使用 `pip install --no-index --find-links=packages/ -r requirements.txt`，`packages/` 目录已包含所有 wheel 文件
4. **cryptography版本** — 必须锁定 42.0.8，46.0.0 会导致旧版交换机SSH协商失败
5. **SCP/SFTP状态** — 工具自动检查并开启，已开启的设备跳过；状态记录在Excel `scp_status` 列，重复执行不会重复开启
6. **文件校验方式** — H3C使用 `md5sum` 命令精确校验MD5；华为通过 `dir` 查看文件大小间接验证；锐捷使用 `verify` 命令校验
7. **Y/N确认** — 工具全局自动检测并回复Y，覆盖 install activate / install commit / patch load / save 等所有交互
8. **H3C大缓冲区** — H3C设备使用40MB paramiko Transport接收窗口，适应display命令大量输出
9. **H3C命令视图** — install activate/commit 在用户视图执行，不进config模式（`config_mode_required: false`）
10. **旧版SSH兼容** — 部分旧版H3C/华为交换机仅支持 SHA1 密钥交换和 ssh-rsa 主机密钥，cryptography 42.0.8 + paramiko 3.5.1 默认支持这些算法
11. **死循环保护** — 所有while循环最多60次迭代后强制退出，防止设备输出异常导致无限等待
12. **保存配置** — 默认不save，给人工验证留窗口期。可在配置文件设置 `save: true` 或用 `--save` 覆盖
13. **回退操作** — 工具只生成回退命令文件，不自动执行。需人工审核后登录设备手动执行
14. **并发控制** — `workers` 控制并发数，建议生产环境不超过10，避免对设备管理口造成压力
15. **密码安全** — 密码可存于配置文件（建议设置文件权限600），也可通过CLI参数传递（执行后清理Shell历史）
16. **二次开发** — 所有源码均有完整中文注释，YAML模板可直接编辑，方便适配其他厂商或自定义流程
