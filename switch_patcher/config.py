"""
配置文件加载模块
- 从patch_config.yaml读取执行参数（Excel路径、Sheet名、用户名密码等）
- 支持CLI参数覆盖配置文件值
- 提供gen_config()生成示例配置文件
- 参数优先级：CLI参数 > 配置文件 > 默认值
"""

import yaml
from pathlib import Path

# 示例配置文件内容
SAMPLE_CONFIG = """\
# Switch Patcher 补丁执行配置文件
# 执行命令：python -m switch_patcher（自动读取此文件）
# CLI参数会覆盖此文件中的对应值

# === 必填项 ===
excel: ""                     # Excel文件路径（相对或绝对路径）
sheet: ""                      # 要处理的Sheet名称（灰度分批）
username: ""                   # SSH用户名（所有设备统一）
password: ""                   # SSH密码（所有设备统一）

# === 可选项 ===
ssh_port: 22                   # SSH端口
workers: 5                     # 最大并发设备连接数
timeout: 30                    # 单条命令SSH超时秒数
patches_dir: patches           # 补丁文件存放目录
save: false                    # 激活后是否自动保存配置
dry_run: false                 # 预检查模式（只做健康检查，不传输不激活）
"""


def load_config(config_path: str = "patch_config.yaml") -> dict:
    """
    加载YAML配置文件
    - 文件不存在则返回空字典（由CLI参数和默认值兜底）
    - 返回: 配置字典
    """
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if data else {}


def gen_config(output_path: str = "patch_config.yaml") -> str:
    """
    生成示例配置文件
    - 如果文件已存在则不覆盖，只返回提示
    - 返回: 生成的文件路径或提示信息
    """
    path = Path(output_path)
    if path.exists():
        return f"配置文件已存在: {path}（如需重新生成请先删除）"
    path.write_text(SAMPLE_CONFIG, encoding="utf-8")
    return f"已生成示例配置文件: {path.absolute()}"
