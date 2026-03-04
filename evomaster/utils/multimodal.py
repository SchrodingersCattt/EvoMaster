"""多模态工具：图片 base64 编码与多模态内容块构建（与上游 EvoMaster 多模态能力对齐）。"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


def encode_image_to_base64(
    image_path: str | Path,
    *,
    mime_type: str | None = None,
) -> str:
    """将本地图片文件编码为 base64 的 data URL。

    Args:
        image_path: 图片文件路径（支持 PNG、JPG、JPEG、GIF、WEBP）。
        mime_type: 可选，如 "image/png"；不传则根据后缀推断。

    Returns:
        data URL 字符串，形如 "data:image/png;base64,..."
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")
    suffix = path.suffix.lower()
    if mime_type is None:
        mime_map = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }
        mime_type = mime_map.get(suffix, 'image/png')
    raw = path.read_bytes()
    b64 = base64.standard_b64encode(raw).decode('ascii')
    return f"data:{mime_type};base64,{b64}"


def build_multimodal_content(
    text: str,
    image_paths: list[str] | None = None,
) -> str | list[dict[str, Any]]:
    """构建用于 LLM API 的多模态 content。

    当无图片时返回纯文本；有图片时返回内容块列表 [{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"data:..."}}]。

    Args:
        text: 文本描述。
        image_paths: 图片文件路径列表；None 或空则仅返回 text。

    Returns:
        str 或 list[dict]，可直接作为 UserMessage.content 或 API messages[].content。
    """
    if not image_paths:
        return text
    blocks: list[dict[str, Any]] = []
    if t := text.strip():
        blocks.append({'type': 'text', 'text': t})
    for p in image_paths:
        url = encode_image_to_base64(p)
        blocks.append({'type': 'image_url', 'image_url': {'url': url}})
    return blocks if blocks else text
