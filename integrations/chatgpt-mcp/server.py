from __future__ import annotations

import os
import posixpath
from pathlib import PurePosixPath

import asyncssh
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("AndroidHarness Workspace (read-only)", stateless_http=True)
HOST = os.environ.get("ANDROIDHARNESS_SSH_HOST", "").strip()
USER = os.environ.get("ANDROIDHARNESS_SSH_USER", "").strip()
KEY = os.environ.get("ANDROIDHARNESS_SSH_KEY", "").strip()
KNOWN_HOSTS = os.environ.get("ANDROIDHARNESS_KNOWN_HOSTS", "").strip()
ROOT = os.environ.get("ANDROIDHARNESS_WORKSPACE", "").strip()
MAX_BYTES = int(os.environ.get("ANDROIDHARNESS_MAX_READ_BYTES", "200000"))


def relative(path: str) -> str:
    p = PurePosixPath(path or ".")
    if p.is_absolute() or ".." in p.parts or "\x00" in path or len(path) > 1024:
        raise ValueError("Path must be workspace-relative and cannot contain traversal.")
    return "." if str(p) in ("", ".") else p.as_posix()


def configured() -> None:
    missing = [k for k, v in (("ANDROIDHARNESS_SSH_HOST", HOST), ("ANDROIDHARNESS_SSH_USER", USER), ("ANDROIDHARNESS_SSH_KEY", KEY), ("ANDROIDHARNESS_KNOWN_HOSTS", KNOWN_HOSTS), ("ANDROIDHARNESS_WORKSPACE", ROOT)) if not v]
    if missing:
        raise RuntimeError("Missing configuration: " + ", ".join(missing))
    if not ROOT.startswith("/") or ROOT == "/":
        raise RuntimeError("ANDROIDHARNESS_WORKSPACE must be a non-root absolute path")
    if not 1 <= MAX_BYTES <= 2000000:
        raise RuntimeError("ANDROIDHARNESS_MAX_READ_BYTES must be 1..2000000")


async def open_sftp():
    configured()
    conn = await asyncssh.connect(HOST, username=USER, client_keys=[KEY], known_hosts=KNOWN_HOSTS, agent_path=None, password=None, connect_timeout=10)
    try:
        return conn, await conn.start_sftp_client()
    except Exception:
        conn.close()
        await conn.wait_closed()
        raise


def inside(path: str) -> bool:
    root = posixpath.normpath(ROOT)
    candidate = posixpath.normpath(path)
    return candidate == root or candidate.startswith(root.rstrip("/") + "/")


async def safe_target(sftp, rel: str) -> str:
    """Reject symlinks in the configured root and every path component."""
    root = posixpath.normpath(ROOT)
    current = "/"
    for component in [part for part in root.split("/") if part]:
        current = posixpath.join(current, component)
        attrs = await sftp.lstat(current)
        if attrs.type == asyncssh.FILEXFER_TYPE_SYMLINK:
            raise ValueError("Symlink component in configured workspace root.")
        if current != root and attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY:
            raise ValueError("Workspace root contains a non-directory component.")
    target = root
    for component in ([] if rel == "." else rel.split("/")):
        target = posixpath.join(target, component)
        attrs = await sftp.lstat(target)
        if attrs.type == asyncssh.FILEXFER_TYPE_SYMLINK:
            raise ValueError("Symlink paths are not allowed.")
        if component != (rel.split("/")[-1] if rel != "." else "") and attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY:
            raise ValueError("Path component is not a directory.")
    if not inside(target):
        raise ValueError("Path escapes configured workspace.")
    return target


@mcp.tool()
async def workspace_list(path: str = ".") -> dict:
    """List entries beneath the configured workspace root. Symlinks are not traversable."""
    rel = relative(path)
    conn, sftp = await open_sftp()
    try:
        target = await safe_target(sftp, rel)
        attrs = await sftp.lstat(target)
        if attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY:
            return {"ok": False, "error": "Target is not a directory."}
        entries = []
        async for name, item in sftp.scandir(target):
            if item.type == asyncssh.FILEXFER_TYPE_SYMLINK:
                kind = "symlink (not traversable)"
            elif item.type == asyncssh.FILEXFER_TYPE_DIRECTORY:
                kind = "directory"
            elif item.type == asyncssh.FILEXFER_TYPE_REGULAR:
                kind = "file"
            else:
                kind = "other"
            entries.append({"name": name, "type": kind})
            if len(entries) >= 500:
                break
        return {"ok": True, "path": rel, "entries": sorted(entries, key=lambda x: x["name"])}
    except (OSError, asyncssh.SFTPError) as exc:
        return {"ok": False, "error": str(exc)[:2000]}
    finally:
        conn.close()
        await conn.wait_closed()


@mcp.tool()
async def workspace_read_file(path: str) -> dict:
    """Read a bounded UTF-8 text file beneath the configured workspace root."""
    rel = relative(path)
    conn, sftp = await open_sftp()
    try:
        target = await safe_target(sftp, rel)
        attrs = await sftp.lstat(target)
        if attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
            return {"ok": False, "error": "Only regular files can be read."}
        if attrs.size is not None and attrs.size > MAX_BYTES:
            return {"ok": False, "error": "File exceeds configured read limit."}
        async with sftp.open(target, "rb") as handle:
            data = await handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return {"ok": False, "error": "File exceeds configured read limit."}
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {"ok": False, "error": "File is not UTF-8 text."}
        return {"ok": True, "path": rel, "content": text}
    except (OSError, asyncssh.SFTPError) as exc:
        return {"ok": False, "error": str(exc)[:2000]}
    finally:
        conn.close()
        await conn.wait_closed()


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8000, path="/mcp")
