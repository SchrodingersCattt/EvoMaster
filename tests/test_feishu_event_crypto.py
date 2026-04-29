"""飞书事件：签名校验与解析单元测试。"""

import json

from src.utils.feishu_event_crypto import (
    feishu_sha256_signature,
    parse_event_json,
    verify_lark_signature,
)


def test_feishu_signature_matches_doc_order() -> None:
    ts = '123'
    nonce = 'abc'
    key = 'test_key'
    body = b'{"hello":"world"}'
    sig = feishu_sha256_signature(ts, nonce, key, body)
    assert len(sig) == 64
    assert verify_lark_signature(ts, nonce, key, body, sig) is True
    assert verify_lark_signature(ts, nonce, key, body, 'deadbeef') is False


def test_parse_plain_url_verification() -> None:
    raw = json.dumps(
        {
            'challenge': 'xh',
            'token': 'tok',
            'type': 'url_verification',
        }
    ).encode()
    p = parse_event_json(raw, None)
    assert p['type'] == 'url_verification'
    assert p['challenge'] == 'xh'
