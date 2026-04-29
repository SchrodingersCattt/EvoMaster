"""飞书事件订阅：签名校验、AES 解密（与开放平台文档一致）。"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from Crypto.Cipher import AES
except ImportError:
    AES = None  # type: ignore[misc, assignment]


def feishu_sha256_signature(
    timestamp: str,
    nonce: str,
    encrypt_key: str,
    body: bytes,
) -> str:
    """X-Lark-Signature：sha256(timestamp + nonce + encrypt_key + raw_body) hex。"""
    b1 = (timestamp + nonce + encrypt_key).encode("utf-8")
    b = b1 + body
    return hashlib.sha256(b).hexdigest()


def verify_lark_signature(
    timestamp: str | None,
    nonce: str | None,
    encrypt_key: str,
    body: bytes,
    signature: str | None,
) -> bool:
    if not signature or not timestamp or not nonce:
        return False
    expect = feishu_sha256_signature(timestamp, nonce, encrypt_key, body)
    return expect == signature


class FeishuAESCipher:
    """Encrypt Key 派生密钥，AES-256-CBC，与官方 Python 示例一致。"""

    def __init__(self, encrypt_key: str) -> None:
        if AES is None:
            raise RuntimeError("pycryptodome is required for Feishu event decrypt")
        self._bs = AES.block_size
        self._key = hashlib.sha256(FeishuAESCipher._str_to_bytes(encrypt_key)).digest()

    @staticmethod
    def _str_to_bytes(data: str | bytes) -> bytes:
        if isinstance(data, str):
            return data.encode("utf-8")
        return data

    @staticmethod
    def _unpad(s: bytes) -> bytes:
        if not s:
            return s
        pad = s[-1]
        if isinstance(pad, int) and 0 < pad <= 16:
            return s[:-pad]
        return s

    def decrypt_string(self, b64: str) -> str:
        raw = base64.b64decode(b64)
        iv = raw[: self._bs]
        enc = raw[self._bs :]
        cipher = AES.new(self._key, AES.MODE_CBC, iv)
        dec = cipher.decrypt(enc)
        text = self._unpad(dec).decode("utf-8")
        # 部分示例从首个 { 到最后一个 } 截取 JSON
        left = text.find("{")
        right = text.rfind("}")
        if left != -1 and right != -1 and right >= left:
            return text[left : right + 1]
        return text


def decrypt_event_body(encrypt_key: str, encrypt_b64: str) -> dict[str, Any]:
    cipher = FeishuAESCipher(encrypt_key)
    plain = cipher.decrypt_string(encrypt_b64)
    return json.loads(plain)


def parse_event_json(body: bytes, encrypt_key: str | None) -> dict[str, Any]:
    """解析 POST body：明文 JSON 或 {"encrypt": "..."}。"""
    try:
        obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning("feishu parse_event_json invalid json: %s", e)
        raise
    if isinstance(obj, dict) and "encrypt" in obj:
        if not encrypt_key:
            raise ValueError("event body is encrypted but FEISHU_ENCRYPT_KEY is empty")
        inner = decrypt_event_body(encrypt_key, str(obj["encrypt"]))
        return inner
    if isinstance(obj, dict):
        return obj
    raise ValueError("unexpected event body type")
