"""Pico session path helpers (Apache-2.0)."""
import re
from pathlib import Path

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')

def ensure_dir(path: Path) -> Path:
    """确保 Directory 存在，并返回同一个 `Path`。

    使用 `parents=True` 创建缺失父目录，`exist_ok=True` 允许目录已经存在。若目标存在但不是目录、或
    OS 拒绝创建，异常原样传播；函数不清空已有内容，也不改变权限。
    """
    path.mkdir(parents=True, exist_ok=True)
    return path

def safe_filename(name: str) -> str:
    """把 Unsafe Path Characters 替换为 Underscores。

    ``<>:\"/\\|?*`` 会统一折叠成 ``_``，随后移除两端空白。转换不是可逆编码，不同原字符串可能
    得到同一 Filename；调用方若需要稳定唯一性，应另外加入 ID 或 Digest。
    """
    return _UNSAFE_CHARS.sub("_", name).strip()
