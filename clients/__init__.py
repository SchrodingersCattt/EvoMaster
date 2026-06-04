"""外部服务 HTTP 客户端集合（与 src/ matmaster/ utils/ 并列的顶层共享层）。

这里放"向外部服务发请求的瘦客户端"，例如 matmaster-tools-server 计费上报。
本包只依赖 aiohttp + utils 等基础设施，不依赖 matmaster / src 业务，因此可被
src（线上 Worker）、matmaster.devshell（评测外壳）、evaluation 共同依赖，且不触发
matmaster 对 src 的反向 import（见 tests/matmaster/test_import_audit.py）。
"""
