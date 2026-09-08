"""Private, atomic credential storage; Windows payloads use user-bound DPAPI."""

import base64
import ctypes
import errno
import json
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


class AuthError(RuntimeError):
    """An authentication error whose message excludes credential values."""

    def __init__(self, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer)
    target = Blob()
    if decrypt:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.POINTER(wintypes.LPWSTR),
                              ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(Blob)]
        description = None
    else:
        operation = crypt.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR,
                              ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(Blob)]
        description = "nreact OpenAI credentials"
    operation.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN: token operations never display an OS prompt.
    if not operation(ctypes.byref(source), description, None, None, None, 1, ctypes.byref(target)):
        raise AuthError("Windows could not protect or unlock the nreact credential cache.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


class CredentialStore:
    """Read, replace and clear one nreact-owned cache, without touching Codex."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().absolute()

    def _check_path(self) -> None:
        if self.path.is_symlink():
            raise AuthError("Credential cache symlinks are unsupported.")

    def load(self) -> dict | None:
        self._check_path()
        try:
            with self.path.open("rb") as reader:
                info = os.fstat(reader.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise AuthError("Credential cache must be a regular file.")
                if os.name != "nt" and info.st_mode & 0o077:
                    raise AuthError("Credential cache permissions must be 0600. Restrict access before using it.")
                raw = reader.read(131_073)
        except FileNotFoundError:
            return None
        except OSError:
            raise AuthError("Could not read the nreact credential cache.") from None
        if len(raw) > 131_072:
            raise AuthError("Credential cache exceeds 128 KB.")
        try:
            envelope = json.loads(raw)
            if not isinstance(envelope, dict) or envelope.get("version") != 1:
                raise ValueError
            encoding = envelope.get("encoding")
            if encoding == "dpapi" and os.name == "nt":
                payload = json.loads(_dpapi(base64.b64decode(envelope["data"], validate=True), decrypt=True))
            elif encoding == "plain" and os.name != "nt":
                payload = envelope["data"]
            else:
                raise AuthError("This credential cache belongs to a different operating system. Sign in again.")
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise AuthError("Invalid nreact credential cache. Sign in again.") from None

    @contextmanager
    def locked(self):
        """Serialize cache changes and rotating-token refresh across processes."""
        self._check_path()
        lock_path = self.path.with_name(self.path.name + ".lock")
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if lock_path.is_symlink():
                raise AuthError("Credential lock symlinks are unsupported.")
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(descriptor, "r+b") as lock:
                if os.fstat(lock.fileno()).st_size == 0:
                    lock.write(b"\0")
                    lock.flush()
                deadline = time.monotonic() + 45
                while True:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            lock.seek(0)
                            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as exc:
                        if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                            raise
                        if time.monotonic() >= deadline:
                            raise AuthError("The nreact credential cache is busy. Try again shortly.") from None
                        time.sleep(0.1)
                try:
                    yield
                finally:
                    if os.name == "nt":
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError:
            raise AuthError("Could not lock or update the nreact credential cache.") from None

    def save(self, payload: dict) -> None:
        """Atomically replace the cache; callers must hold locked()."""
        self._check_path()
        if os.name == "nt":
            encoded = _dpapi(json.dumps(payload, allow_nan=False).encode("utf-8"))
            envelope = {"version": 1, "encoding": "dpapi", "data": base64.b64encode(encoded).decode("ascii")}
        else:
            envelope = {"version": 1, "encoding": "plain", "data": payload}
        raw = json.dumps(envelope, allow_nan=False).encode("utf-8")
        if len(raw) > 131_072:
            raise AuthError("Credential cache exceeds 128 KB.")
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".nreact-auth-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(descriptor, "wb") as writer:
                writer.write(raw)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise AuthError("Could not save the nreact credential cache.") from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def clear(self) -> bool:
        """Remove only this cache; callers must hold locked()."""
        self._check_path()
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            raise AuthError("Could not clear the nreact credential cache.") from None
