from __future__ import annotations

import asyncio
import hashlib
import json
import os
import posixpath
import re
import secrets
import shlex
from pathlib import PurePosixPath

import asyncssh
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("AndroidHarness Workspace Bridge", stateless_http=True)
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
    root_components = [part for part in root.split("/") if part]
    for index, component in enumerate(root_components):
        current = posixpath.join(current, component)
        attrs = await sftp.lstat(current)
        if attrs.type == asyncssh.FILEXFER_TYPE_SYMLINK:
            raise ValueError("Symlink component in configured workspace root.")
        if index < len(root_components) - 1 and attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY:
            raise ValueError("Workspace root contains a non-directory component.")
    if attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY:
        raise ValueError("Configured workspace root is not a directory.")
    target = root
    rel_components = [] if rel == "." else rel.split("/")
    for index, component in enumerate(rel_components):
        target = posixpath.join(target, component)
        attrs = await sftp.lstat(target)
        if attrs.type == asyncssh.FILEXFER_TYPE_SYMLINK:
            raise ValueError("Symlink paths are not allowed.")
        if index < len(rel_components) - 1 and attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY:
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


@mcp.tool()
async def workspace_write_file(path: str, content: str) -> dict:
    """Write UTF-8 text inside the configured workspace; rejects symlinks and limits writes."""
    rel = relative(path)
    data = content.encode("utf-8")
    if len(data) > MAX_BYTES:
        return {"ok": False, "error": "Content exceeds configured write limit."}
    if rel == ".":
        return {"ok": False, "error": "A file path is required."}
    conn, sftp = await open_sftp()
    try:
        parent_rel = posixpath.dirname(rel) or "."
        parent = await safe_target(sftp, parent_rel)
        parent_attrs = await sftp.lstat(parent)
        if parent_attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY:
            return {"ok": False, "error": "Parent path is not a directory."}
        target = posixpath.join(parent, posixpath.basename(rel))
        try:
            attrs = await sftp.lstat(target)
            if attrs.type == asyncssh.FILEXFER_TYPE_SYMLINK:
                return {"ok": False, "error": "Symlink targets are not allowed."}
            if attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
                return {"ok": False, "error": "Only regular files can be overwritten."}
        except asyncssh.SFTPNoSuchFile:
            pass
        temporary = posixpath.join(parent, ".cin-write-" + secrets.token_hex(12))
        try:
            async with sftp.open(temporary, "xb") as handle:
                await handle.write(data)
            await sftp.posix_rename(temporary, target)
        finally:
            try:
                await sftp.remove(temporary)
            except asyncssh.SFTPError:
                pass
        return {"ok": True, "path": rel, "bytes_written": len(data)}
    except (OSError, asyncssh.SFTPError) as exc:
        return {"ok": False, "error": str(exc)[:2000]}
    finally:
        conn.close()
        await conn.wait_closed()


@mcp.tool()
async def git_status() -> dict:
    """Return read-only git status for the configured workspace."""
    configured()
    conn = await asyncssh.connect(
        HOST, username=USER, client_keys=[KEY], known_hosts=KNOWN_HOSTS,
        agent_path=None, password=None, connect_timeout=10,
    )
    try:
        result = await conn.run(
            "git -C " + shlex.quote(posixpath.normpath(ROOT)) +
            " status --short --branch",
            check=False, timeout=15,
        )
        output = (result.stdout or "")[:MAX_BYTES]
        error = (result.stderr or "")[:2000]
        return {"ok": result.exit_status == 0, "exit_code": result.exit_status,
                "stdout": output, "stderr": error}
    finally:
        conn.close()
        await conn.wait_closed()


@mcp.tool()
async def git_diff() -> dict:
    """Return a bounded read-only git diff for the configured workspace."""
    configured()
    conn = await asyncssh.connect(
        HOST, username=USER, client_keys=[KEY], known_hosts=KNOWN_HOSTS,
        agent_path=None, password=None, connect_timeout=10,
    )
    try:
        result = await conn.run(
            "git -C " + shlex.quote(posixpath.normpath(ROOT)) +
            " diff --no-ext-diff --",
            check=False, timeout=20,
        )
        output = (result.stdout or "")[:MAX_BYTES]
        error = (result.stderr or "")[:2000]
        return {"ok": result.exit_status == 0, "exit_code": result.exit_status,
                "stdout": output, "stderr": error,
                "truncated": len(result.stdout or "") > MAX_BYTES}
    finally:
        conn.close()
        await conn.wait_closed()



# The canonical CIN-Core engine remains on the authorized Termux node.
# These tools only transport requests through the existing pinned SSH link
# to the localhost-only Monolith One bridge; they never execute SSH commands.
CIN_BRIDGE_PORT = os.environ.get("ANDROIDHARNESS_CIN_BRIDGE_PORT", "6974")
CIN_MAX_RESPONSE_BYTES = 512_000


def cin_bridge_port() -> int:
    try:
        port = int(CIN_BRIDGE_PORT)
    except ValueError as exc:
        raise ValueError("Invalid CIN bridge port") from exc
    if not 1024 <= port <= 65535:
        raise ValueError("CIN bridge port must be 1024..65535")
    return port


async def cin_bridge_http(method: str, route: str, payload: dict | None = None) -> tuple[int, dict]:
    """HTTP/1.1 over the authenticated SSH connection to the *remote* loopback.

    No public bridge listener, arbitrary SSH command, shell, or remote file
    mutation is introduced. The existing bridge's S99/4A checks still apply.
    """
    configured()
    if (method, route) not in (("GET", "/status"), ("POST", "/exchange")):
        raise ValueError("Unsupported CIN bridge operation")
    body = b"" if payload is None else json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(body) > MAX_BYTES:
        raise ValueError("CIN request exceeds configured size limit")
    request = (
        f"{method} {route} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Accept: application/json\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + body
    conn = await asyncssh.connect(
        HOST, username=USER, client_keys=[KEY], known_hosts=KNOWN_HOSTS,
        agent_path=None, password=None, connect_timeout=10,
    )
    writer = None
    try:
        reader, writer = await conn.open_connection(
            "127.0.0.1", cin_bridge_port(), encoding=None
        )
        writer.write(request)
        await writer.drain()
        response = bytearray()
        # The canonical Python bridge returns Content-Length and closes.
        # Limit bytes even if a faulty or malicious peer streams forever.
        while True:
            chunk = await asyncio.wait_for(reader.read(8192), timeout=100)
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > CIN_MAX_RESPONSE_BYTES + 8192:
                raise ValueError("CIN bridge response exceeds size limit")
        header, delimiter, response_body = bytes(response).partition(b"\r\n\r\n")
        if not delimiter or len(header) > 8192:
            raise ValueError("Malformed CIN bridge HTTP response")
        header_lines = header.decode("ascii").split("\r\n")
        status_parts = header_lines[0].split(" ")
        if len(status_parts) < 2 or status_parts[0] not in ("HTTP/1.0", "HTTP/1.1"):
            raise ValueError("Invalid CIN bridge HTTP status")
        status = int(status_parts[1])
        headers = {}
        for line in header_lines[1:]:
            if ":" not in line:
                raise ValueError("Malformed CIN bridge response header")
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip().lower()
        if "transfer-encoding" in headers:
            raise ValueError("Unexpected CIN bridge transfer encoding")
        if "content-length" not in headers:
            raise ValueError("CIN bridge omitted response length")
        expected = int(headers["content-length"])
        if expected < 0 or expected > CIN_MAX_RESPONSE_BYTES or len(response_body) != expected:
            raise ValueError("CIN bridge response length mismatch")
        data = json.loads(response_body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Unexpected CIN bridge JSON response")
        return status, data
    finally:
        if writer is not None:
            writer.close()
        conn.close()
        await conn.wait_closed()


@mcp.tool()
async def cin_runtime_status() -> dict:
    """Check the actual CIN-Core Monolith One localhost bridge via pinned SSH."""
    try:
        status, result = await cin_bridge_http("GET", "/status")
        return {
            "ok": status == 200, "http_status": status,
            "bridge": result.get("bridge"), "engine": result.get("engine"),
            "runtime_bridge_verified": result.get("runtime_bridge_verified") is True,
            "runtime_bridge_verification_source": result.get("runtime_bridge_verification_source"),
            "note": "Bridge reachability is not proof of execution for a conversation turn.",
        }
    except (OSError, ValueError, RuntimeError, asyncssh.Error, asyncio.TimeoutError) as exc:
        return {"ok": False, "runtime_bridge_verified": False,
                "error": f"{type(exc).__name__}: {str(exc)[:500]}"}


@mcp.tool()
async def cin_runtime_exchange(content: str, conversation_id: str = "chatgpt-mcp") -> dict:
    """Submit a turn to local Omega83 and return its correlated response or timeout.

    No advisory text is marked as device-executed without the canonical
    bridge's explicit, cryptographically checked runtime attestation.
    """
    if not isinstance(content, str) or not content.strip():
        return {"ok": False, "error": "content must be nonempty text"}
    if len(content.encode("utf-8")) > min(MAX_BYTES - 2000, 100_000):
        return {"ok": False, "error": "CIN turn exceeds the configured size limit"}
    if not isinstance(conversation_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", conversation_id):
        return {"ok": False, "error": "invalid conversation_id"}
    payload = {
        "content": content, "conversation_id": conversation_id,
        "direction": "app_to_cin", "source": "AndroidHarness ChatGPT MCP",
        "target": "CIN-Core Omega 8.3", "voice_output": "none",
    }
    try:
        status, result = await cin_bridge_http("POST", "/exchange", payload)
        turn = result.get("request_turn_id")
        request_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        if status != 200:
            return {
                "ok": False, "http_status": status,
                "queued": result.get("queued") is True,
                "request_turn_id": turn, "error": result.get("error") or result.get("detail")
                or result.get("status") or "CIN bridge request failed",
                "runtime_execution_verified": False,
            }
        runtime_payload = result.get("runtime_payload")
        if not isinstance(runtime_payload, dict) or not isinstance(turn, str) or not re.fullmatch(r"[0-9a-f]{32}", turn):
            raise ValueError("Missing correlated CIN runtime payload")
        if (result.get("conversation_id") != conversation_id or
                result.get("request_content_hash") != request_hash or
                runtime_payload.get("request_turn_id") != turn or
                runtime_payload.get("request_content_hash") != request_hash):
            raise ValueError("CIN response did not match the submitted turn")
        answer = result.get("response")
        if not isinstance(answer, str):
            raise ValueError("CIN returned an invalid response")
        # Only the locally verified attestation is authoritative. Raw flags
        # inside the runtime response are not self-authenticating.
        verified = result.get("runtime_attestation_verified") is True
        if not verified:
            return {
                "ok": False, "verification_state": "unverified",
                "runtime_execution_verified": False,
                "action_execution_verified": False,
                "request_turn_id": turn,
                "error": "CIN response has no verified local Circuit 9 attestation",
            }
        return {
            "ok": True, "response": answer,
            "conversation_id": conversation_id, "request_turn_id": turn,
            "request_content_hash": request_hash,
            "runtime_execution_verified": verified,
            "action_execution_verified": verified and result.get("action_execution_verified") is True,
            "execution_owner": "OMEGA83" if verified else None,
            "verification_state": "device_attested" if verified else "unverified",
        }
    except (OSError, ValueError, RuntimeError, asyncssh.Error, asyncio.TimeoutError) as exc:
        return {"ok": False, "runtime_execution_verified": False,
                "error": f"{type(exc).__name__}: {str(exc)[:500]}"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8000, path="/mcp")

