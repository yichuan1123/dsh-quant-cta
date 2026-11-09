"""凭据的本机加密存储。

Windows 上用 DPAPI（CryptProtectData）：密文只能被**同一台机器上的同一个用户**
解开——文件被拷到别的机器、别的账户即失效。这是「常态化存储」该有的强度：
不用你每次输密码，也不把密码明文摊在磁盘上。

非 Windows 或 DPAPI 调用失败时退化为明文，并在状态里如实标注 storage=plain，
不做「假装加密了」这种事。
"""

import base64
import ctypes
import sys

SCHEME_DPAPI = "dpapi-v1"
SCHEME_PLAIN = "plain"

_CRYPT = None


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt():
    """惰性初始化 crypt32/kernel32 的函数原型（只在 Windows 上有效）。"""
    global _CRYPT
    if _CRYPT is not None:
        return _CRYPT
    if not sys.platform.startswith("win"):
        _CRYPT = False
        return _CRYPT
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB), ctypes.c_wchar_p, ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DATA_BLOB),
        ]
        crypt32.CryptProtectData.restype = ctypes.c_int
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DATA_BLOB),
        ]
        crypt32.CryptUnprotectData.restype = ctypes.c_int
        _CRYPT = (crypt32, kernel32)
    except Exception:
        _CRYPT = False
    return _CRYPT


def _blob(data: bytes) -> tuple:
    buf = ctypes.create_string_buffer(data, len(data))
    return (_DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf)


def dpapi_available() -> bool:
    return bool(_crypt())


def dpapi_protect(text: str) -> str:
    """加密并返回 base64 密文。失败抛 OSError。"""
    libs = _crypt()
    if not libs:
        raise OSError("DPAPI 不可用")
    crypt32, kernel32 = libs
    raw = text.encode("utf-8")
    blob_in, keep = _blob(raw)
    blob_out = _DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(blob_in), None, None, None, None, 0,
                                    ctypes.byref(blob_out)):
        raise OSError("CryptProtectData 失败")
    try:
        cipher = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
    return base64.b64encode(cipher).decode("ascii")


def dpapi_unprotect(b64: str) -> str:
    libs = _crypt()
    if not libs:
        raise OSError("DPAPI 不可用")
    crypt32, kernel32 = libs
    cipher = base64.b64decode(b64.encode("ascii"))
    blob_in, keep = _blob(cipher)
    blob_out = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0,
                                      ctypes.byref(blob_out)):
        raise OSError("CryptUnprotectData 失败（可能是别的用户/机器加密的）")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(blob_out.pbData)


def protect(text: str) -> tuple:
    """返回 (密文或原文, scheme)。优先 DPAPI，失败则如实退化为明文。"""
    if dpapi_available():
        try:
            return dpapi_protect(text), SCHEME_DPAPI
        except OSError:
            pass
    return text, SCHEME_PLAIN


def unprotect(stored: str, scheme: str) -> str:
    if scheme == SCHEME_DPAPI:
        return dpapi_unprotect(stored)
    return stored
