from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _credential_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ConstructionOwnerClassifier"
    return root / "zhipu_api_key.dat"


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("API Key 加密仅支持 Windows DPAPI")
    source, source_buffer = _blob(data)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "ConstructionOwnerClassifier", None, None, None, 0,
        ctypes.byref(output),
    ):
        raise ctypes.WinError()
    del source_buffer
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("API Key 解密仅支持 Windows DPAPI")
    source, source_buffer = _blob(data)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    del source_buffer
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def save_api_key(api_key: str, path: str | Path | None = None) -> None:
    value = api_key.strip()
    if not value:
        raise ValueError("API Key 不能为空")
    target = _credential_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(_protect(value.encode("utf-8")))
    os.replace(temporary, target)


def load_api_key(path: str | Path | None = None) -> str:
    target = _credential_path(path)
    if not target.is_file():
        return ""
    return _unprotect(target.read_bytes()).decode("utf-8")


def clear_api_key(path: str | Path | None = None) -> None:
    target = _credential_path(path)
    if target.exists():
        target.unlink()


def mask_api_key(value: str) -> str:
    value = value.strip()
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"
