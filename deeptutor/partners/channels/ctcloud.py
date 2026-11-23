"""Ctcloud channel — 天翼云电脑内智能体 IM 渠道（deskclawbot 新流程 + gRPC）.

接入协议见《天翼云电脑内智能体IM接入方案》；注册编排结构与重试策略
参考另一项目的同协议实装（.agents/tmp/bgzs/bgzs-account.mjs）。

依赖为可选：grpcio / cryptography 缺失时渠道标记 unavailable，不影响其他渠道。
"""

import asyncio
import base64
import hashlib
import json
import os
import random as _random
import ssl
import string
import struct
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from deeptutor.partners.bus.events import OutboundMessage
from deeptutor.partners.bus.queue import MessageBus
from deeptutor.partners.channels.base import BaseChannel
from deeptutor.partners.config.schema import Base

try:
    import grpc
    import grpc.aio as grpc_aio
    GRPC_AVAILABLE = True
except ImportError:
    grpc = None
    grpc_aio = None
    GRPC_AVAILABLE = False

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


CTCLOUD_AVAILABLE = GRPC_AVAILABLE  # 媒体收发缺 cryptography 可降级为文本；gRPC 是硬依赖


# ── 常量（协议 + 参考实装）─────────────────────────────────────────

AGENT_TYPE = "deeptutor"  # 硬编码：向平台申请的智能体标识（用户决策）

ENDPOINT_GET_AUTH_STR = "/portal/thirdapp/v1/deskclawbot/getAuthStr"
ENDPOINT_REGISTER = "/portal/thirdapp/v1/deskclawbot/register"

JSSDK_WS_ENDPOINT = "ws://127.0.0.1:9002"  # 云电脑客户端本地 jssdk 网关

# 两层重试（参考实装）：内层取票、外层阶段，共用一条总时限
TICKET_MAX_RETRIES = 5
PHASE_MAX_RETRIES = 2
PROVISION_DEADLINE_S = 90.0

_TICKET_RETRY_DELAYS = (0.0, 1.0, 2.0, 4.0, 8.0)
_PHASE_RETRY_DELAYS = (0.0, 3.0, 6.0)

DEDUP_CACHE_SIZE = 1000

# 会话签名用的客户端版本号：YL-Main-Version。参考实装默认 '1.0.0'，
# 签名串里也拼同一字符串，两处必须一致否则验签失败
GRPC_MAIN_VERSION = "1.0.0"

# jssdk 建连用的固定 SDK apiKey（参考实装 DESKCLAWBOT_SDK_API_KEY）。
# jssdk 签名 sha256(appid+secret+t+type) 以它作 appid，与 agentType 无关。
DESKCLAWBOT_SDK_API_KEY = "89b04e0d49547005e04bb5b8ea3d92b1"

# yimu 媒体容器常量（与云电脑客户端 Services.file 逐字节对齐，参考实装）
YIMU_MAGIC = b"yimu"
YIMU_CHUNK_BYTES = 64 * 1024
YIMU_ZERO_IV = b"\x00" * 16


class CtcloudError(Exception):
    """渠道级错误基类，携带结构化 code 供 setup_state 文案归类。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CtcloudHttpError(CtcloudError):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(code, message)
        self.status = status


class CtcloudProvisioningError(CtcloudError):
    """注册编排失败。retryable=False 的失败不烧重试预算，直接引导用户。"""

    def __init__(self, code: str, message: str, retryable: bool = True):
        super().__init__(code, message)
        self.retryable = retryable


class CtcloudGrpcError(CtcloudError):
    def __init__(self, code: str, message: str, grpc_status: str | None = None):
        super().__init__(code, message)
        self.grpc_status = grpc_status


# ── 配置 ──────────────────────────────────────────────────────────
#
# 开发者注：CtcloudConfig 必须是 BaseModel —— WebUI 表单由
# GET /api/partners/channels/schema 从本模型自动渲染（约定模块内存在
# ``XxxConfig`` BaseModel）。类 docstring 会成为表单顶部描述（面向用户，
# 只写一句话）。

# 接入参数硬编码（与 AGENT_TYPE 同类，均为用户决策）：不做表单/配置项。
# 真实凭据不落源码仓库 —— Desktop 打包时由构建脚本
# （apps/desktop/scripts/build-runtime.mjs）按环境变量生成
# _ctcloud_secrets.py 注入 bundled core；源码/开发环境回落占位符。
DEFAULT_API_BASE_URL = "https://openyilian.ctyun.cn"
DEFAULT_GRPC_SERVER_ADDRESS = "clawyilian.ctyun.cn:2000"
ALLOW_FROM = ("*",)  # 允许对话的用户 open_id 白名单；"*" 允许全部

try:
    from deeptutor.partners.channels import _ctcloud_secrets
    AGENT_APP_CODE = _ctcloud_secrets.AGENT_APP_CODE
    AGENT_APP_SECRET = _ctcloud_secrets.AGENT_APP_SECRET
except ImportError:  # 源码 checkout / 未注入构建：占位符
    AGENT_APP_CODE = "ak"  # 应用标识（YL-3rd-Appcode）占位符
    AGENT_APP_SECRET = "sk"  # 签名密钥占位符

class CtcloudConfig(Base):
    """接入天翼云电脑：启用即用，接入参数已内置。"""

    enabled: bool = Field(default=False, description="启用天翼云电脑渠道。")

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return cls().model_dump(by_alias=False)


# ── 签名 ──────────────────────────────────────────────────────────

def _random8(rng: _random.Random | None = None) -> str:
    r = rng or _random
    alphabet = string.ascii_letters + string.digits
    return "".join(r.choice(alphabet) for _ in range(8))


def sign_yunlian(
    *,
    url_params: dict[str, Any] | None,
    sk: str,
    timestamp_ms: int,
    random8: str,
    tail_fields: list[str],
) -> str:
    """翼连体系签名（3.1.x / 3.2.x 共用骨架）。

    SHA256(排序URL参数 & sk & tail...)，tail 依次为：
      - 接入期 HTTP：[YL-Timestamp, YL-Random, YL-3rd-Appcode]
      - gRPC 会话期：[YL-Timestamp, YL-Random, YL-Device-Id, YL-Main-Version]
    本函数只负责串序；各调用方传入对应 tail。timestamp 由调用方拼进 tail。
    """
    parts: list[str] = []
    if url_params:
        for key in sorted(url_params):
            parts.append(f"{key}={url_params[key]}")
    parts.append(sk)
    parts.extend(tail_fields)
    payload = "&".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_provision_headers(
    url_params: dict[str, Any] | None, app_code: str, sk: str
) -> dict[str, str]:
    """3.1.1 / 3.1.3 接入期请求头（YL-* 四件套）。"""
    ts = int(time.time() * 1000)
    rnd = _random8()
    signature = sign_yunlian(
        url_params=url_params,
        sk=sk,
        timestamp_ms=ts,
        random8=rnd,
        tail_fields=[str(ts), rnd, app_code],
    )
    return {
        "YL-Signature": signature,
        "YL-Timestamp": str(ts),
        "YL-Random": rnd,
        "YL-3rd-Appcode": app_code,
    }


# Linux 云桌面身份文件：seed 即桌面 uuid（与参考实装 BGZS_LINUX_DATASOURCE_PATH 一致）
LINUX_DATASOURCE_PATH = "/var/lib/cloud/instance/datasource"


def read_desktop_uuid() -> str:
    """读云桌面 uuid，供 YL-Desktop-Uuid 使用。

    参考实装逻辑：Linux 取 datasource 中的 seed=...（不走 ctmeta）。
    RBAC 按桌面 uuid 绑定机器人与桌面/账号；读不到该头时服务端直接
    PERMISSION_DENIED（RBAC: access denied）——因此这里读不到时抛错，
    而不是静默不发。
    """
    import re

    try:
        content = Path(LINUX_DATASOURCE_PATH).read_text("utf-8")
    except OSError as e:
        raise CtcloudProvisioningError(
            "desktop-unavailable",
            f"云桌面 uuid 读取失败（{LINUX_DATASOURCE_PATH}: {type(e).__name__}）。"
            "容器部署需将该文件挂载进容器；非云桌面环境无法接入。",
            retryable=False,
        ) from e
    m = re.search(r"seed=([^\]\s]+)", content)
    if not m:
        raise CtcloudProvisioningError(
            "desktop-unavailable",
            f"云桌面 uuid 缺失（{LINUX_DATASOURCE_PATH} 中无 seed= 字段）",
            retryable=False,
        )
    return m.group(1)


def build_grpc_metadata(
    *, sk: str, device_id: str, authorization: str = "",
    url_params: dict[str, Any] | None = None,
    desktop_uuid: str = "",
) -> list[tuple[str, str]]:
    """gRPC 调用 6 头 metadata（YL-Authorization + 5 项签名参与项）。

    key 必须全小写：gRPC 规范限制自定义 metadata key 为小写 ASCII，
    大写 key 会被 validate_metadata 拒绝（INTERNAL: Invalid metadata）。
    协议文档里的 YL-* 命名仅指语义，线上一律小写。
    authorization（register 返回的 ylAuthorization）参考实装按 jwt 处理，
    统一包 `Bearer ` 前缀后发 YL-Authorization；签名串不含它。
    desktop_uuid：云桌面 uuid，作为 YL-Desktop-Uuid 一并发出，RBAC 按它
    绑定桌面与账号，缺失时服务端回 PERMISSION_DENIED —— 因此必传。
    x-client-trace-id：平台方按它查服务端日志。
    """
    ts = int(time.time() * 1000)
    rnd = _random8()
    signature = sign_yunlian(
        url_params=url_params,
        sk=sk,
        timestamp_ms=ts,
        random8=rnd,
        tail_fields=[str(ts), rnd, device_id, str(GRPC_MAIN_VERSION)],
    )
    trace_id = str(uuid.uuid4())
    metadata = [
        ("yl-authorization", f"Bearer {authorization}".strip()),  # 参考实装：jwt/ylAuthorization 必须带 Bearer 前缀
        ("yl-signature", signature),
        ("yl-timestamp", str(ts)),
        ("yl-random", rnd),
        ("yl-device-id", device_id),
        ("yl-main-version", GRPC_MAIN_VERSION),
        ("x-client-trace-id", trace_id),
    ]
    metadata.append(("yl-desktop-uuid", desktop_uuid))
    return metadata


def _jwt_exp(token: str) -> str:
    """从 ylAuthorization (JWT) 解出 exp/iat，供过期排查。解不出返回 "-"。"""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        exp = payload.get("exp")
        if exp is None:
            return "-"
        return f"{exp} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(exp)))})"
    except Exception:
        return "-"


def _credential_fingerprint(secret: str) -> str:
    """凭据脱敏指纹：长度 + md5 前 8 位。给后端核对 signKey 用，不泄值。"""
    return f"len={len(secret)} md5={hashlib.md5(secret.encode('utf-8')).hexdigest()[:8]}"

# ── yimu 媒体容器加解密（AES-128-CBC，64KiB 分片，零 IV）─────────
#
# 云电脑客户端的既定容器格式（参考实装逐字节移植）：
#   | "yimu" | 4B BE 头部配置长度 | base64 头部配置 |
#   | 重复 [ 4B BE 密文长度 | AES-128-CBC(分片, 零IV, PKCS7) 密文 ] |
# 头部配置固定为 {"encode":true,"version":1} 的 base64。协议文档只写了
# "AES 加密、16 字节密钥"，实际线上格式以此为准，ECB 整段加密收发都对不上。

def _normalize_secret_key(secret_key: str) -> bytes:
    """非 16 字节密钥取 MD5 前 16 字节（客户端 normalizeSecretKey 行为）。"""
    key = secret_key.encode("utf-8")
    if len(key) == 16:
        return key
    return hashlib.md5(key).digest()[:16]


def _new_random_secret_key() -> str:
    """出站密钥：16 位 ASCII。客户端把它直接当 16 字节 AES 密钥用。"""
    alphabet = string.ascii_letters + string.digits
    return "".join(_random.choice(alphabet) for _ in range(16))


def aes_encrypt(data: bytes, key: bytes) -> bytes:
    if not CRYPTO_AVAILABLE:
        raise CtcloudError("crypto-unavailable", "cryptography 库未安装，无法收发媒体")
    if len(key) != 16:
        raise CtcloudError("crypto-bad-key", "AES 密钥必须为 16 字节（128 位）")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    header = base64.b64encode(
        json.dumps({"encode": True, "version": 1}, separators=(",", ":")).encode("utf-8")
    )
    parts = [YIMU_MAGIC, len(header).to_bytes(4, "big"), header]
    for offset in range(0, len(data), YIMU_CHUNK_BYTES):
        chunk = data[offset:offset + YIMU_CHUNK_BYTES]
        enc = Cipher(algorithms.AES(key), modes.CBC(YIMU_ZERO_IV)).encryptor()
        encrypted = enc.update(chunk) + enc.finalize()
        parts.append(len(encrypted).to_bytes(4, "big"))
        parts.append(encrypted)
    return b"".join(parts)


def aes_decrypt(data: bytes, key: bytes) -> bytes:
    if not CRYPTO_AVAILABLE:
        raise CtcloudError("crypto-unavailable", "cryptography 库未安装，无法收发媒体")
    if len(key) != 16:
        raise CtcloudError("crypto-bad-key", "AES 密钥必须为 16 字节（128 位）")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(data) < len(YIMU_MAGIC) or data[: len(YIMU_MAGIC)] != YIMU_MAGIC:
        raise CtcloudError("crypto-bad-container", "入站媒体缺少 yimu 容器魔数")

    offset = len(YIMU_MAGIC)
    (header_len,) = struct.unpack_from(">I", data, offset)
    offset += 4 + header_len
    chunks = []
    while offset < len(data):
        (chunk_len,) = struct.unpack_from(">I", data, offset)
        offset += 4
        if offset + chunk_len > len(data):
            raise CtcloudError("crypto-bad-container", "yimu 分片长度越界")
        encrypted = data[offset:offset + chunk_len]
        offset += chunk_len
        dec = Cipher(algorithms.AES(key), modes.CBC(YIMU_ZERO_IV)).decryptor()
        chunks.append(dec.update(encrypted) + dec.finalize())
    return b"".join(chunks)


# ── jssdk 取票（云电脑客户端本地 ws 网关，XXTEA 加密帧）───────────

# jssdk 协议常量（与云电脑客户端内嵌 jssdk 逐字对齐，来自参考实装）
JSSDK_TYPE = 9            # 建连 query 的 type 参数
JSSDK_XKEY_B64 = "GQyexCTqBn7Jxjl9Xm1wFuxCAvEL+GRX"   # XXTEA 帧密钥（base64）
JSSDK_SECRET_B64 = "tWpFZJ+DOWnkAg9EKh34MsBR9CX/adJZBgBAcYRv9ho="  # 建连 sign 派生密钥


def _xxtea_to_uint32_array(data: str, include_length: bool) -> list[int]:
    n = len(data) >> 2
    if len(data) & 3:
        n += 1
    if include_length:
        v = [0] * (n + 1)
        v[n] = len(data)
    else:
        v = [0] * n
    for i, ch in enumerate(data):
        v[i >> 2] |= ord(ch) << ((i & 3) << 3)
    return v


def _xxtea_to_binary_string(v: list[int], include_length: bool) -> str | None:
    length = len(v)
    n = length << 2
    if include_length:
        m = v[length - 1]
        n -= 4
        if m < n - 3 or m > n:
            return None
        n = m
    # 与参考实装一致：每个 uint32 拆成 4 个字符（低字节在前）
    parts: list[str] = []
    for i in range(length):
        word = v[i]
        parts.append(chr(word & 0xFF))
        parts.append(chr((word >> 8) & 0xFF))
        parts.append(chr((word >> 16) & 0xFF))
        parts.append(chr((word >> 24) & 0xFF))
    result = "".join(parts)
    if include_length:
        return result[:n]
    return result


def _mx(sum_: int, y: int, z: int, p: int, e: int, k: list[int]) -> int:
    return (
        (((z >> 5) ^ ((y << 2) & 0xFFFFFFFF)) + ((y >> 3) ^ ((z << 4) & 0xFFFFFFFF)))
        ^ ((sum_ ^ y) + (k[(p & 3) ^ e] ^ z))
    )


def _xxtea_encrypt_uint32(v: list[int], k: list[int]) -> list[int]:
    n = len(v) - 1
    z = v[n]
    sum_ = 0
    q = (6 + 52 // len(v)) & 0x7FFFFFFF
    for _ in range(q):
        sum_ = (sum_ + 0x9E3779B9) & 0xFFFFFFFF
        e = (sum_ >> 2) & 3
        for p in range(n):
            y = v[p + 1]
            z = v[p] = (v[p] + _mx(sum_, y, z, p, e, k)) & 0xFFFFFFFF
        y = v[0]
        z = v[n] = (v[n] + _mx(sum_, y, z, n, e, k)) & 0xFFFFFFFF
    return v


def _xxtea_decrypt_uint32(v: list[int], k: list[int]) -> list[int]:
    n = len(v) - 1
    y = v[0]
    q = (6 + 52 // len(v)) & 0x7FFFFFFF
    sum_ = (q * 0x9E3779B9) & 0xFFFFFFFF
    while sum_ != 0:
        e = (sum_ >> 2) & 3
        for p in range(n, 0, -1):
            z = v[p - 1]
            y = v[p] = (v[p] - _mx(sum_, y, z, p, e, k)) & 0xFFFFFFFF
        z = v[n]
        y = v[0] = (v[0] - _mx(sum_, y, z, 0, e, k)) & 0xFFFFFFFF
        sum_ = (sum_ - 0x9E3779B9) & 0xFFFFFFFF
    return v


def _xxtea_utf8_encode(s: str) -> str:
    # 与参考实装一致：latin-1 风格逐字节展开（参考实装 utf8Encode 的行为）
    return s.encode("utf-8").decode("latin-1")


def _xxtea_utf8_decode(bs: str) -> str:
    return bs.encode("latin-1").decode("utf-8", errors="replace")


def _xxtea_encrypt_to_hex(data: str, key: str) -> str:
    data = _xxtea_utf8_encode(data)
    key = _xxtea_utf8_encode(key)
    k = _xxtea_to_uint32_array(key.ljust(16, "\x00"), False)[:4]
    if len(k) < 4:
        k += [0] * (4 - len(k))
    v = _xxtea_to_uint32_array(data, True)
    encrypted = _xxtea_encrypt_uint32(v, k)
    bs = _xxtea_to_binary_string(encrypted, False)
    return bs.encode("latin-1").hex()


def _xxtea_decrypt_from_hex(data: str, key: str) -> str:
    key = _xxtea_utf8_encode(key)
    k = _xxtea_to_uint32_array(key.ljust(16, "\x00"), False)[:4]
    if len(k) < 4:
        k += [0] * (4 - len(k))
    raw = bytes.fromhex(data).decode("latin-1")
    v = _xxtea_to_uint32_array(raw, False)
    decrypted = _xxtea_decrypt_uint32(v, k)
    bs = _xxtea_to_binary_string(decrypted, True)
    if bs is None:
        raise ValueError("xxtea decrypt: bad padding")
    return _xxtea_utf8_decode(bs)


def _jssdk_derive_key(b64_value: str) -> str:
    """解出 jssdk 内嵌的 XXTEA 密钥字符串。

    参考实装用 XXTEA.decryptFromBase64(b64, CHARS) 派生密钥：内嵌常量本身
    是 XXTEA 密文，密钥为固定字符表 CHARS。必须走完整解密，不能只做 base64。
    """
    chars = "0123456789abcdefghijklmnopqrstuvwxyzQWERTYUIOPLKJHGFDSAZXCVBNM"
    raw = base64.b64decode(b64_value).decode("latin-1")
    decrypted = _xxtea_decrypt_from_hex(raw.encode("latin-1").hex(), chars)
    return decrypted


async def request_ticket_via_jssdk(
    auth_str: str,
    api_key: str,
    *,
    logger_: Any = logger,
) -> str:
    """连接 ws://127.0.0.1:9002，methodName 4 取 ticket（新流程 type:0 票据）。

    帧协议（与云电脑客户端内嵌 jssdk 对齐，参考 bgzs-jssdk.mjs 实装）：
    1. 建连 URL 必须带签名 query：?appid=<apiKey>&t=<ms>&sign=<sha256>&type=9
       sign = sha256(appid + secret + t + type)，secret 为客户端内嵌固定值。
       裸连会被网关直接关闭（ConnectionClosedError 的根因之一）。
    2. 请求/响应帧均为 XXTEA 加密后的 hex 字符串（非明文 JSON），密钥为
       客户端内嵌 xKey。明文帧会被网关直接断连。
    3. methodName 4（authRequestWithTicketNew）帧必须携带 type: 0 ——
       新流程要求 type:0 票据；methodName 3 的票据 register 能过、gRPC
       connect 却报 13 INTERNAL（实装踩坑）。
    """
    try:
        import websockets
    except ImportError as e:
        raise CtcloudProvisioningError(
            "ws-unavailable", "websockets 库未安装，无法连接云电脑客户端取票", retryable=False
        ) from e

    try:
        t = int(time.time() * 1000)
        secret = _jssdk_derive_key(JSSDK_SECRET_B64)
        sign = hashlib.sha256(
            f"{api_key}{secret}{t}{JSSDK_TYPE}".encode("utf-8")
        ).hexdigest()
        url = (
            f"{JSSDK_WS_ENDPOINT}?appid={api_key}&t={t}"
            f"&sign={sign}&type={JSSDK_TYPE}"
        )
        x_key = _jssdk_derive_key(JSSDK_XKEY_B64)

        async with websockets.connect(url, open_timeout=5) as ws:
            request = {
                "methodName": 4,  # authRequestWithTicketNew（新流程票据）
                "apiKey": api_key,
                "authStr": auth_str,
                "type": 0,  # 新流程票据类型标记，缺了拿到的票 register 消费不了
            }
            await ws.send(_xxtea_encrypt_to_hex(json.dumps(request), x_key))
            raw = await asyncio.wait_for(ws.recv(), timeout=9)
            response = json.loads(_xxtea_decrypt_from_hex(str(raw), x_key))

            ticket = response.get("ticket") or ""
            if not ticket:
                # 只记短字段与字段名清单，绝不打印整个响应（ticket 是凭据）
                keys = ",".join(response.keys()) if isinstance(response, dict) else "?"
                code = response.get("code") if isinstance(response, dict) else None
                logger_.warning(
                    "ctcloud jssdk ticket response carried no ticket (code={}, keys={})",
                    code, keys,
                )
                raise CtcloudProvisioningError(
                    "jssdk-no-ticket", "云电脑客户端未返回 ticket"
                )
            return ticket
    except CtcloudProvisioningError:
        raise
    except (asyncio.TimeoutError, OSError, Exception) as e:
        # 连接层失败：客户端多半还没就绪（渠道加载时自动发起，用户可能之后才打开）
        raise CtcloudProvisioningError(
            "jssdk-failed", f"云电脑客户端取票失败: {type(e).__name__}"
        ) from e


# ── 渠道 ──────────────────────────────────────────────────────────

@dataclass
class _Credentials:
    """register 一步返回的 gRPC 鉴权三要素。"""

    claw_bot_account_id: str
    sign_key: str
    yl_authorization: str


@dataclass
class _PendingSend:
    """出站消息的 UUID 幂等句柄：ChannelManager 重试复用同一 UUID。

    consumed=True 表示平台已受理（packetId 已落库）；此后重试必须换新
    UUID，否则按已处理重复请求被吞。
    """

    client_message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    attempts: int = 0
    consumed: bool = False


class _TlsTunnel:
    """本地 TLS 隧道：127.0.0.1 明文入口 ←→ 远端 TLS 网关。

    为什么需要：天翼 gRPC 网关是 TLS 服务但不回 ALPN（selected=None），
    grpc C-core 的 grpc_ssl_check_alpn 强制要求 ALPN=h2 且无运行时关闭
    开关（仅编译期宏 TSI_OPENSSL_ALPN_SUPPORT）。Python ssl 模块不强制
    ALPN，因此用它在本地终结 TLS，gRPC 走 insecure_channel 连隧道口。

    建连策略：gRPC 是短连接池式的，每次 RPC 可能新开 subchannel；隧道
    按 accept 循环逐条转发，每条 client socket 独立对接一条远端 TLS 连接。
    """

    def __init__(self, target_host: str, target_port: int, ca_file: Path | None):
        self._target_host = target_host
        self._target_port = target_port
        self._ca_file = ca_file
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.local_port: int = 0

    @classmethod
    async def start(cls, target: str, ca_file: Path | None) -> "_TlsTunnel":
        host, _, port = target.rpartition(":")
        tunnel = cls(host or target, int(port or 443), ca_file)
        tunnel._server = await asyncio.start_server(
            tunnel._handle_client, "127.0.0.1", 0
        )
        tunnel.local_port = tunnel._server.sockets[0].getsockname()[1]
        logger.info("ctcloud TLS tunnel listening on 127.0.0.1:{}", tunnel.local_port)
        return tunnel

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("ctcloud TLS tunnel stopped")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            ssl_ctx = ssl.create_default_context(cafile=str(self._ca_file)) if self._ca_file else ssl.create_default_context()
            # 网关证书与域名不匹配（实测 SAN 不含 clawyilian.ctyun.cn），
            # 保留 CA 链校验但放宽主机名校验；TLS 加密本身不受影响。
            ssl_ctx.check_hostname = False
            if self._ca_file is None:
                # 无 CA 兜底：完全不校验（与旧 insecure 行为对齐）
                ssl_ctx.verify_mode = ssl.CERT_NONE
            remote_reader, remote_writer = await asyncio.open_connection(
                self._target_host, self._target_port, ssl=ssl_ctx,
                server_hostname=self._target_host,
            )
            async def pipe(src: asyncio.StreamReader, dst_writer: asyncio.StreamWriter) -> None:
                try:
                    while True:
                        chunk = await src.read(65536)
                        if not chunk:
                            break
                        dst_writer.write(chunk)
                        await dst_writer.drain()
                except (ConnectionError, asyncio.CancelledError, ssl.SSLError):
                    pass
                finally:
                    try:
                        dst_writer.close()
                    except Exception:
                        pass

            await asyncio.gather(
                pipe(reader, remote_writer),
                pipe(remote_reader, writer),
                return_exceptions=True,
            )
        except (OSError, ssl.SSLError) as e:
            logger.warning("ctcloud TLS tunnel connection failed: {}", e)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


class CtcloudChannel(BaseChannel):
    """天翼云电脑 IM 渠道。

    start()：凭证装载（state_dir）→ 缺凭证则自动三步注册（两层重试）→ gRPC 收流。
    send()：OutboundMessage → SendMessageRequest（6 头签名 + UUID 幂等）。
    协议无消息编辑 API，不覆写 send_delta，走缓冲交付。
    """

    name = "ctcloud"
    display_name = "天翼云电脑"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = CtcloudConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: CtcloudConfig = config
        self._credentials: _Credentials | None = None
        self._desktop_uuid: str = ""
        self._grpc_stub: Any = None
        self._grpc_channel: Any = None
        self._recv_task: asyncio.Task[None] | None = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._pending_sends: OrderedDict[str, _PendingSend] = OrderedDict()
        self._http: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tunnel: "_TlsTunnel | None" = None

    def is_allowed(self, sender_id: str) -> bool:
        """白名单硬编码（原为配置字段，现与 AGENT_TYPE 同类常量）。"""
        if not ALLOW_FROM:
            return False
        if "*" in ALLOW_FROM:
            return True
        return str(sender_id) in ALLOW_FROM

    def _state_path(self):
        return self.state_dir() / "credentials.json"

    # ── HTTP 客户端（留默认值，防装配缺陷伪装成网络错误）──────────

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self._http

    # ── 凭证装载与注册编排 ────────────────────────────────────────

    def _load_credentials(self) -> _Credentials | None:
        path = self._state_path()
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
            creds = _Credentials(
                claw_bot_account_id=str(data.get("clawBotAccountId") or ""),
                sign_key=str(data.get("signKey") or ""),
                yl_authorization=str(data.get("ylAuthorization") or ""),
            )
            if creds.claw_bot_account_id and creds.sign_key and creds.yl_authorization:
                return creds
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("ctcloud credentials file unreadable: {}", e)
        return None

    def _save_credentials(self, creds: _Credentials) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "clawBotAccountId": creds.claw_bot_account_id,
                "signKey": creds.sign_key,
                "ylAuthorization": creds.yl_authorization,
            }, ensure_ascii=False, indent=2),
            "utf-8",
        )
        logger.info("ctcloud credentials saved to {}", path)

    async def _provision(self) -> _Credentials:
        """三步注册编排，两层重试共用一条 90s 时限。

        外层每轮从 getAuthStr 重走：上轮失败多半因 authStr/registerToken
        作废、ticket 已被消费，只有重走才能拿到全新一批凭据。
        """
        base = DEFAULT_API_BASE_URL
        app_code = AGENT_APP_CODE
        sk = AGENT_APP_SECRET
        if not app_code or not sk:
            raise CtcloudProvisioningError(
                "config-incomplete",
                "AGENT_APP_CODE / AGENT_APP_SECRET 常量为空，无法发起注册",
                retryable=False,
            )

        deadline = time.monotonic() + PROVISION_DEADLINE_S
        last_error: CtcloudError | None = None

        for phase_attempt, phase_delay in enumerate(_PHASE_RETRY_DELAYS):
            if phase_delay:
                await asyncio.sleep(phase_delay)
            if time.monotonic() > deadline:
                break
            try:
                return await self._provision_once(base, app_code, sk, deadline)
            except CtcloudProvisioningError as e:
                if not e.retryable:
                    raise
                last_error = e
                logger.warning(
                    "ctcloud provision phase {} failed: {} (code={})",
                    phase_attempt + 1, e, e.code,
                )
            except CtcloudError as e:
                last_error = e
                logger.warning("ctcloud provision phase {} failed: {}", phase_attempt + 1, e)

        raise CtcloudProvisioningError(
            "provision-exhausted",
            f"注册编排重试耗尽（{PHASE_MAX_RETRIES + 1} 轮）：{last_error}",
        )

    async def _provision_once(
        self, base: str, app_code: str, sk: str, deadline: float
    ) -> _Credentials:
        # 步骤 1：getAuthStr
        params = {"agentType": AGENT_TYPE}
        headers = build_provision_headers(params, app_code, sk)
        http = self._get_http()
        resp = await http.get(
            f"{base}{ENDPOINT_GET_AUTH_STR}",
            params=params,
            headers=headers,
        )
        if resp.status_code == 401:
            raise CtcloudProvisioningError(
                "signature-rejected", "YL-Signature 校验失败（401）：核对串序与系统时钟",
                retryable=False,
            )
        if resp.status_code == 403:
            raise CtcloudProvisioningError(
                "authorization-rejected", "YL-3rd-Appcode 校验失败（403）",
                retryable=False,
            )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        auth_str = str(data.get("authStr") or "")
        register_token = str(data.get("registerToken") or "")
        if not auth_str or not register_token:
            raise CtcloudProvisioningError(
                "getauthstr-incomplete", "getAuthStr 响应缺少 authStr/registerToken"
            )
        logger.info(
            "ctcloud getAuthStr ok (authStr len={}, registerToken len={})",
            len(auth_str), len(register_token),
        )

        # 步骤 2：jssdk 取票（内层重试：每次尝试新建 ws 连接）
        ticket = await self._request_ticket_with_retry(auth_str, deadline)

        # 步骤 3：register
        headers = build_provision_headers({}, app_code, sk)
        resp = await http.post(
            f"{base}{ENDPOINT_REGISTER}",
            json={"ticket": ticket, "agentType": AGENT_TYPE, "registerToken": register_token},
            headers=headers,
        )
        resp.raise_for_status()
        result = resp.json()
        data = result.get("data") or {}
        creds = _Credentials(
            claw_bot_account_id=str(data.get("clawBotAccountId") or ""),
            sign_key=str(data.get("signKey") or ""),
            yl_authorization=str(data.get("ylAuthorization") or ""),
        )
        if not (creds.claw_bot_account_id and creds.sign_key and creds.yl_authorization):
            raise CtcloudProvisioningError(
                "register-incomplete",
                f"register 响应缺少三要素之一（resultCode={result.get('resultCode')}, "
                f"resultMsg={result.get('resultMsg')}）",
            )
        logger.info(
            "ctcloud register ok: claw_bot_account_id={} sign_key={} auth_exp={}",
            creds.claw_bot_account_id,
            _credential_fingerprint(creds.sign_key),
            _jwt_exp(creds.yl_authorization),
        )
        return creds

    async def _request_ticket_with_retry(self, auth_str: str, deadline: float) -> str:
        last_error: CtcloudError | None = None
        for attempt, delay in enumerate(_TICKET_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            if time.monotonic() > deadline:
                break
            try:
                return await request_ticket_via_jssdk(
                    auth_str, DESKCLAWBOT_SDK_API_KEY, logger_=logger
                )
            except CtcloudProvisioningError as e:
                if not e.retryable:
                    raise
                last_error = e
                logger.warning("ctcloud ticket attempt {}/{} failed: {}", attempt + 1, len(_TICKET_RETRY_DELAYS), e)
        raise CtcloudProvisioningError(
            "ticket-exhausted", f"取票重试耗尽（{len(_TICKET_RETRY_DELAYS)} 次）：{last_error}"
        )

    # ── 生命周期 ──────────────────────────────────────────────────

    async def start(self) -> None:
        """凭证装载 → 缺凭证则自动注册 → gRPC 收流。"""
        if not CTCLOUD_AVAILABLE:
            self.set_setup_state(
                "unavailable",
                message="缺少 grpcio 依赖（pip install grpcio），渠道无法启动。",
            )
            return
        if not self.config.enabled:
            self.set_setup_state("action_required", message="渠道未启用（enabled=false）。")
            return
        if not ALLOW_FROM:
            logger.warning("ctcloud: ALLOW_FROM is empty — all access denied")

        self._running = True
        self._loop = asyncio.get_running_loop()

        # RBAC 按桌面 uuid 绑定：容器/非云桌面环境读不到时直接进入终态，
        # 不让 gRPC 带着缺失的身份头反复撞 PERMISSION_DENIED。
        try:
            self._desktop_uuid = read_desktop_uuid()
        except CtcloudError as e:
            self.set_setup_state("action_required", message=str(e))
            self._running = False
            return

        creds = self._load_credentials()
        if creds is not None:
            logger.info(
                "ctcloud loaded cached credentials from {}: account_id={} auth_exp={}",
                self._state_path(), creds.claw_bot_account_id,
                _jwt_exp(creds.yl_authorization),
            )
        if creds is None:
            self.set_setup_state("connecting", message="未发现凭证，正在发起注册编排…")
            try:
                creds = await self._provision()
                self._save_credentials(creds)
            except CtcloudError as e:
                self.set_setup_state(
                    "action_required",
                    message=f"注册编排失败（{e.code}）：{e}。请确认云电脑客户端已启动并登录。",
                )
                self._running = False
                return
        self._credentials = creds
        self.set_setup_state("connecting", message="正在建立 gRPC 长连接…")

        await self._connect_grpc()
        self._recv_task = asyncio.create_task(self._recv_loop())
        self.set_setup_state("running")

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """取消收流 task、关闭 gRPC channel / TLS 隧道与 HTTP 客户端。"""
        self._running = False
        task = self._recv_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._recv_task = None
        if self._grpc_channel is not None:
            await self._grpc_channel.close()
            self._grpc_channel = None
        self._grpc_stub = None
        if self._tunnel is not None:
            await self._tunnel.stop()
            self._tunnel = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._pending_sends.clear()
        logger.info("ctcloud channel stopped")

    # ── 出站发送 ──────────────────────────────────────────────────

    def _pending_for(self, key: str) -> _PendingSend:
        """取（或建）幂等句柄：ChannelManager 重试重发同一 OutboundMessage 时复用 UUID。

        上次已成功（client_message_id 已被消费）则换新 UUID，避免平台
        packetId 查重把重试的新内容吞掉。
        """
        pending = self._pending_sends.get(key)
        if pending is None or pending.consumed:
            pending = _PendingSend()
            self._pending_sends[key] = pending
            while len(self._pending_sends) > DEDUP_CACHE_SIZE:
                self._pending_sends.popitem(last=False)
        pending.attempts += 1
        return pending

    async def _upload_media(self, file_path: str) -> tuple[str, str, int]:
        """出站媒体：随机 16 位 ASCII 密钥 yimu 加密 → multipart 上传 → 下载 url。

        上传端点要求**会话期完整 YL-* 头**（signKey + clawBotAccountId +
        ylAuthorization，实测只带 YL-Authorization 或接入期四件套都 401）。
        协议文档写 url 填 uuid，但接收端实际是按完整下载 URL 拉取
        （参考实装实测：下载走老前缀 /plugins/fileserver/chat/download，
        uuid 是裸相对路径，path 参数不 URL 编码）。
        """
        if not self._credentials:
            raise CtcloudError("not-connected", "凭证缺失，无法上传媒体")
        key = _new_random_secret_key()
        with open(file_path, "rb") as f:
            plain = f.read()
        data = aes_encrypt(plain, key.encode("utf-8"))
        http = self._get_http()
        base = DEFAULT_API_BASE_URL.replace("openyilian", "filesvryilian")
        files = {"file": (os.path.basename(file_path), data)}
        resp = await http.post(
            f"{base}/plugins/fileserver/agent/chat",
            files=files,
            headers=self._grpc_auth_headers_for_upload(),
        )
        resp.raise_for_status()
        entities = resp.json().get("data", {}).get("entities") or []
        if not entities:
            raise CtcloudError("upload-empty", "文件上传响应缺少 entities")
        rel = str(entities[0].get("uuid") or "")
        url = f"{base}/plugins/fileserver/chat/download?path={rel}"
        return url, key, len(plain)

    def _grpc_auth_headers_for_upload(self) -> dict[str, str]:
        """上传端点用的是会话期同套 8 头（值同 gRPC metadata，仅头名原样大小写）。"""
        creds = self._credentials
        assert creds is not None
        meta = build_grpc_metadata(
            authorization=creds.yl_authorization,
            sk=creds.sign_key,
            device_id=creds.claw_bot_account_id,
            desktop_uuid=self._desktop_uuid,
        )
        return {k: v for k, v in meta}

    def _build_outgoing_content(self, text: str) -> Any:
        """出站内容检测：纯文本→Text；md 结构→Markdown(title=首行)。"""
        pb2, _ = self._load_stub()
        stripped = text.strip()
        has_md = any(
            marker in stripped
            for marker in ("```", "\n#", "\n|", "**", "- ", "\n- ")
        )
        if has_md:
            first_line, _, rest = stripped.partition("\n")
            title = first_line.lstrip("#").strip() if first_line.startswith("#") else ""
            body = rest.strip() if title else stripped
            return pb2.OutgoingContent(markdown=pb2.MarkdownContent(title=title, text=body))
        return pb2.OutgoingContent(text=pb2.TextContent(content=stripped))

    async def send(self, msg: OutboundMessage) -> None:
        """OutboundMessage → SendMessageRequest（6 头签名 + UUID 幂等）。

        失败抛异常交 ChannelManager 重试；重试复用同一 UUID 被平台 packetId
        查重吸收，不产生重复消息。
        """
        if not self._grpc_stub or not self._credentials:
            raise CtcloudError("not-connected", "gRPC 未连接，无法发送")

        pb2, _ = self._load_stub()
        creds = self._credentials
        pending = self._pending_for(str(msg.metadata.get("message_id") or msg.chat_id))

        # 媒体先上传换 uuid（消息体里填 uuid，不是公网 URL）。
        # 每条消息（媒体/文本）独立 UUID：服务端按 client_message_id 查重，
        # 共用同一 UUID 时第二条会被当重复请求吞掉。
        for media_path in msg.media:
            if not os.path.isfile(media_path):
                logger.warning("ctcloud media file not found: {}", media_path)
                continue
            file_uuid, key, file_len = await self._upload_media(media_path)
            file_name = os.path.basename(media_path)
            request = pb2.SendMessageRequest(
                target=pb2.ChatTarget(chat=pb2.ChatContext(conversation_id=msg.chat_id)),
                content=pb2.OutgoingContent(
                    file=pb2.FileContent(
                        url=file_uuid,
                        secret_key=key,
                        file_name=file_name,
                        file_length=file_len,  # 明文长度（客户端解密后展示用的尺寸）
                    )
                ),
                client_message_id=str(uuid.uuid4()),
            )
            await self._send_request(request, creds)

        if msg.content and msg.content.strip():
            request = pb2.SendMessageRequest(
                target=pb2.ChatTarget(chat=pb2.ChatContext(conversation_id=msg.chat_id)),
                content=self._build_outgoing_content(msg.content),
                client_message_id=pending.client_message_id,
            )
            await self._send_request(request, creds)
            # 平台已受理：packetId 已落库，后续重试必须换新 UUID
            pending.consumed = True

    def _grpc_auth_metadata(self, creds: _Credentials) -> list[tuple[str, str]]:
        """统一 gRPC 鉴权 metadata：全套 6 头从 build_grpc_metadata 一次生成。

        之前调用侧先手拼裸 yl-authorization 再过滤，容易造成重复头或漏
        Bearer 前缀；参考实装里 jwt/ylAuthorization 的 Bearer 包装是在
        buildMetadata 内部统一做的，这里对齐同样的单出口。
        """
        return build_grpc_metadata(
            authorization=creds.yl_authorization,
            sk=creds.sign_key,
            device_id=creds.claw_bot_account_id,
            desktop_uuid=self._desktop_uuid,
        )

    async def _send_request(self, request: Any, creds: _Credentials) -> None:
        pb2, _ = self._load_stub()
        metadata = self._grpc_auth_metadata(creds)
        try:
            response = await self._grpc_stub.sendMessage(request, metadata=metadata)
        except Exception as e:
            raise CtcloudGrpcError(
                "grpc-send-failed", f"sendMessage 传输失败: {type(e).__name__}"
            ) from e

        result = response.WhichOneof("result")
        if result == "error":
            err = response.error
            raise CtcloudGrpcError(
                "grpc-send-rejected",
                f"sendMessage 被拒: code={err.code}, msg={err.message}",
                grpc_status=str(err.code),
            )
        # accepted：平台已受理，message_id 在 response.accepted.message_id
        logger.debug("ctcloud message accepted: {}", response.accepted.message_id)

    # ── gRPC 连接与收流 ───────────────────────────────────────────

    def _load_stub(self) -> Any:
        """加载生成的 proto stub。proto 模块由 scripts 生成后放入 partners/proto_gen/。"""
        try:
            from deeptutor.partners.proto_gen import useragent_chat_pb2, useragent_chat_pb2_grpc
        except ImportError as e:
            raise CtcloudError(
                "proto-missing",
                "gRPC proto 生成代码缺失，请先运行 scripts/generate_ctcloud_proto.py",
            ) from e
        return useragent_chat_pb2, useragent_chat_pb2_grpc

    async def _connect_grpc(self) -> None:
        pb2, pb2_grpc = self._load_stub()
        address = DEFAULT_GRPC_SERVER_ADDRESS
        # 网关是 TLS 服务（自签 CA）且**不回 ALPN**：grpc C-core 的
        # grpc_ssl_check_alpn 在握手后强制要求 ALPN=h2，服务端不选协议就报
        # "Cannot check peer: missing selected ALPN property"（该检查只受
        # 编译期宏控制，grpcio 无运行时开关）。因此走本地 TLS 隧道：
        # Python ssl 模块建 TLS（不做 ALPN 校验）→ 明文转发到 127.0.0.1，
        # gRPC 用 insecure_channel 连本地端口。
        target = address.removeprefix("https://").removeprefix("http://")
        ca_path = Path(__file__).parent / "yilian_ca.crt"
        self._tunnel = await _TlsTunnel.start(
            target, ca_path if ca_path.is_file() else None
        )
        # 隧道方案下 gRPC 的连接目标是 127.0.0.1:<port>，HTTP/2 :authority
        # 伪头会默认取该值——接入层按 :authority 路由/鉴权，必须显式改写为
        # 真实网关地址（实测 authority=127.0.0.1:xx 无法匹配，鉴权被拒）。
        self._grpc_channel = grpc_aio.insecure_channel(
            f"127.0.0.1:{self._tunnel.local_port}",
            options=[("grpc.default_authority", target)],
        )
        logger.info(
            "ctcloud grpc channel opened: target={} via local TLS tunnel {}",
            target, self._tunnel.local_port,
        )
        self._grpc_stub = pb2_grpc.UserAgentChatServiceStub(self._grpc_channel)

    async def _recv_loop(self) -> None:
        """connect() 收流主循环：断线重连 + 事件分发。

        PERMISSION_DENIED（RBAC: access denied）按参考实装归类为不可自愈：
        机器人未在云台侧授权（或桌面绑定不符），重试一万次也不会变好，
        直接进入终态等人工处理，避免每 5s 刷一次日志。
        """
        pb2, _ = self._load_stub()
        creds = self._credentials
        assert creds is not None
        while self._running:
            try:
                call = self._grpc_stub.connect(
                    pb2.ConnectRequest(), metadata=self._grpc_auth_metadata(creds)
                )
                async for event in call:
                    await self._dispatch_event(event)
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._running:
                    return
                if (
                    GRPC_AVAILABLE
                    and isinstance(e, grpc.aio.AioRpcError)
                    and e.code() == grpc.StatusCode.PERMISSION_DENIED
                ):
                    self._running = False
                    self._log_grpc_denied(e, creds)
                    self.set_setup_state(
                        "action_required",
                        message="RBAC 鉴权被拒绝（PERMISSION_DENIED）：机器人未被授权或桌面绑定不符，"
                        "请在云台侧开通权限后重新启动渠道；密钥/授权无效时需删除凭证重新接入。",
                    )
                    logger.error("ctcloud grpc PERMISSION_DENIED — 机器人未被授权，停止重连")
                    return
                logger.warning(
                    "ctcloud grpc stream error: code={} details={} debug_error_string={} — reconnecting in 5s",
                    getattr(e, "code", lambda: "")(),
                    getattr(e, "details", lambda: "")(),
                    getattr(e, "debug_error_string", lambda: "")(),
                )
                await asyncio.sleep(5)
                # 先停旧隧道再建新隧道，避免每次重连泄漏一个本地监听
                if self._tunnel is not None:
                    await self._tunnel.stop()
                    self._tunnel = None
                if self._grpc_channel is not None:
                    await self._grpc_channel.close()
                    self._grpc_channel = None
                await self._connect_grpc()

    def _log_grpc_denied(self, e: Any, creds: _Credentials) -> None:
        """PERMISSION_DENIED 排查日志（供平台后端定位，凭据只输出脱敏形态）。"""
        logger.error(
            "ctcloud grpc PERMISSION_DENIED: details={!r} debug_error_string={!r} "
            "device_id={} desktop_uuid={} sk_fingerprint={} auth_len={} auth_exp={}",
            getattr(e, "details", lambda: "")(),
            getattr(e, "debug_error_string", lambda: "")(),
            creds.claw_bot_account_id,
            self._desktop_uuid,
            _credential_fingerprint(creds.sign_key),
            len(creds.yl_authorization),
            _jwt_exp(creds.yl_authorization),
        )

    async def _dispatch_event(self, event: Any) -> None:
        """ServerEvent → 去重 → 解析 → _handle_message。"""
        incoming = getattr(event, "received_message", None)
        if incoming is None:
            return  # oneof 其它分支（协议当前仅 received_message）

        message_id = str(incoming.message_id or "")
        if not message_id:
            return
        if message_id in self._processed_message_ids:
            return  # 平台 at-least-once，去重必须有
        self._processed_message_ids[message_id] = None
        while len(self._processed_message_ids) > DEDUP_CACHE_SIZE:
            self._processed_message_ids.popitem(last=False)

        # 会话与发送人：conversation_id 不透明，只存 metadata 原样回传
        chat = incoming.chat
        conversation_id = str(chat.conversation_id or "")
        conversation_type = int(chat.conversation_type or 0)
        sender_open_id = str(incoming.sender.open_id or "")
        sender_nickname = str(incoming.sender.nickname or "")

        if not self.is_allowed(sender_open_id):
            return

        content_parts: list[str] = []
        media_paths: list[str] = []

        content = incoming.content
        body_type = content.WhichOneof("body")
        if body_type == "text":
            text = str(content.text.content or "")
            if text:
                content_parts.append(text)
        elif body_type == "markdown":
            md = content.markdown
            title = str(md.title or "")
            text = str(md.text or "")
            if title:
                content_parts.append(f"# {title}")
            if text:
                content_parts.append(text)
        elif body_type in ("image", "file"):
            path, note = await self._download_and_decrypt_media(
                body_type, getattr(content, body_type)
            )
            if path:
                media_paths.append(path)
            content_parts.append(note)

        content_text = "\n".join(content_parts).strip()
        if not content_text and not media_paths:
            return

        await self._handle_message(
            sender_id=sender_open_id,
            chat_id=conversation_id,  # 不透明路由标识：原样用作 chat_id
            content=content_text,
            media=media_paths,
            metadata={
                "message_id": message_id,
                "conversation_id": conversation_id,
                "conversation_type": conversation_type,
                "sender_nickname": sender_nickname,
                "event_id": str(event.event_id or ""),
            },
        )

    async def _download_and_decrypt_media(self, body_type: str, body: Any) -> tuple[str | None, str]:
        """入站媒体：GET 公网 url 密文 → secret_key AES 解密 → 落盘 media_dir()。"""
        url = str(body.url or "")
        secret_key = str(body.secret_key or "")
        if not url or not secret_key:
            return None, f"[{body_type}: missing url or secret_key]"
        try:
            http = self._get_http()
            # 网关按 URL 里的 secret_key 放行下载（参考实装实测），不追加会被拒
            sep = "&" if "?" in url else "?"
            resp = await http.get(f"{url}{sep}secret_key={secret_key}")
            resp.raise_for_status()
            plain = aes_decrypt(resp.content, _normalize_secret_key(secret_key))

            file_name = str(getattr(body, "file_name", "") or "")
            if not file_name:
                file_name = f"{message_hash(url)}.bin"
            path = self.media_dir() / file_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(plain)
            logger.debug("ctcloud downloaded {} to {}", body_type, path)
            return str(path), f"[{body_type}: {file_name}]"
        except CtcloudError as e:
            logger.warning("ctcloud media decrypt failed: {}", e)
            return None, f"[{body_type}: decrypt failed]"
        except Exception as e:
            logger.warning("ctcloud media download failed: {}", e)
            return None, f"[{body_type}: download failed]"


def message_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


# 渠道注册：registry 自动发现 BaseChannel 子类，无需额外注册代码
