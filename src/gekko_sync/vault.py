"""Windows DPAPI, scoped to the current Windows user. No plaintext fallback."""
import ctypes
import os
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _convert(value: bytes, encrypt: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("El almacenamiento de credenciales requiere Windows y DPAPI.")
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("Windows no pudo proteger o recuperar las credenciales de esta sesión.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def protect(text: str) -> bytes:
    return _convert(text.encode("utf-8"), True)


def unprotect(data: bytes) -> str:
    return _convert(data, False).decode("utf-8")
