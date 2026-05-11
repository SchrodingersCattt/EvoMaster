# Bohrium Transfer StoreHost Contract Test

This test exercises the real StoreHost multipart data plane. It is intentionally
opt-in because it uploads 110MB of random data and requires live StoreHost
credentials.

Use a disposable prefix. The test writes random objects and transfer manifests
under that prefix and under the local pytest temp directory.

```bash
MATMASTER_BOHRIUM_TRANSFER_CONTRACT=1 \
BOHRIUM_STORE_HOST="https://<store-host>" \
BOHRIUM_STORE_TOKEN="<store-token>" \
BOHRIUM_STORE_PREFIX="<disposable-prefix>" \
uv run --extra dev pytest tests/matmaster_bohrium_transfer/test_storehost_contract.py -q -s
```

The fixture is generated with `os.urandom(110 * 1024 * 1024)`, not a sparse
file. The contract gate verifies multipart business-code handling,
`Content-MD5` usage, complete/download success, SHA256 equality for downloaded
objects, concurrent uploads with distinct transfer ids, and absence of raw token
material from manifests.
