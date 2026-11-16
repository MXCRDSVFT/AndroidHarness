import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import asyncssh

import server


class RelativePathTests(unittest.TestCase):
    def test_dot_is_workspace_root(self):
        self.assertEqual(server.relative("."), ".")

    def test_normal_relative_path_is_preserved(self):
        self.assertEqual(server.relative("src/main.py"), "src/main.py")

    def test_rejects_parent_traversal(self):
        for value in ("../secret", "src/../../secret"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.relative(value)

    def test_rejects_absolute_path(self):
        with self.assertRaises(ValueError):
            server.relative("/etc/passwd")

    def test_rejects_nul_and_oversized_paths(self):
        with self.assertRaises(ValueError):
            server.relative("src/\x00secret")
        with self.assertRaises(ValueError):
            server.relative("a" * 1025)


class WorkspaceBoundaryTests(unittest.TestCase):
    def test_inside_accepts_root_and_child(self):
        with patch.object(server, "ROOT", "/srv/androidharness"):
            self.assertTrue(server.inside("/srv/androidharness"))
            self.assertTrue(server.inside("/srv/androidharness/app/Main.kt"))

    def test_inside_rejects_sibling_prefix(self):
        with patch.object(server, "ROOT", "/srv/androidharness"):
            self.assertFalse(server.inside("/srv/androidharness-evil/secret"))

    def test_configuration_requires_known_hosts_and_workspace(self):
        names = ("HOST", "USER", "KEY", "KNOWN_HOSTS", "ROOT")
        with patch.multiple(server, **{name: "" for name in names}):
            with self.assertRaisesRegex(RuntimeError, "ANDROIDHARNESS_SSH_HOST"):
                server.configured()


class SafeTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_symlink_in_workspace_root(self):
        sftp = MagicMock()
        sftp.lstat = AsyncMock(return_value=MagicMock(
            type=asyncssh.FILEXFER_TYPE_SYMLINK
        ))
        with patch.object(server, "ROOT", "/srv/androidharness"):
            with self.assertRaisesRegex(ValueError, "Symlink component"):
                await server.safe_target(sftp, ".")


class WriteToolValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_rejects_workspace_root(self):
        with patch.object(server, "open_sftp", new_callable=AsyncMock) as open_sftp:
            result = await server.workspace_write_file(".", "not a path")
        self.assertFalse(result["ok"])
        open_sftp.assert_not_awaited()

    async def test_write_rejects_oversized_content_before_connecting(self):
        with patch.object(server, "MAX_BYTES", 3), patch.object(
            server, "open_sftp", new_callable=AsyncMock
        ) as open_sftp:
            result = await server.workspace_write_file("a.txt", "four")
        self.assertFalse(result["ok"])
        self.assertIn("write limit", result["error"])
        open_sftp.assert_not_awaited()



class CINRuntimeExchangeTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_bridge_http_10_response(self):
        payload = json.dumps({"bridge": "cin_omega83_conversation_bridge"}).encode("utf-8")
        raw = (
            b"HTTP/1.0 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(payload)}\r\n".encode("ascii")
            + b"\r\n" + payload
        )
        reader = MagicMock()
        reader.read = AsyncMock(side_effect=[raw, b""])
        writer = MagicMock()
        writer.drain = AsyncMock()
        conn = MagicMock()
        conn.open_connection = AsyncMock(return_value=(reader, writer))
        conn.wait_closed = AsyncMock()
        with patch.object(server, "configured"), patch.object(
            server.asyncssh, "connect", new=AsyncMock(return_value=conn)
        ):
            code, body = await server.cin_bridge_http("GET", "/status")
        self.assertEqual(code, 200)
        self.assertEqual(body["bridge"], "cin_omega83_conversation_bridge")
        self.assertEqual(conn.open_connection.await_args.args[0], "127.0.0.1")

    async def test_runtime_flags_without_attestation_remain_unverified(self):
        content = "local hello"
        import hashlib
        request_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        response = {
            "status": "ok",
            "response": "Omega reply",
            "conversation_id": "cin-check",
            "request_content_hash": request_hash,
            "request_turn_id": "a" * 32,
            "runtime_execution_verified": True,
            "runtime_payload": {
                "request_turn_id": "a" * 32,
                "request_content_hash": request_hash,
                "runtime_execution_verified": True,
            },
        }
        with patch.object(server, "cin_bridge_http", new=AsyncMock(return_value=(200, response))):
            outcome = await server.cin_runtime_exchange(content, "cin-check")
        self.assertFalse(outcome["ok"])
        self.assertFalse(outcome["runtime_execution_verified"])
        self.assertNotIn("response", outcome)
        self.assertEqual(outcome["verification_state"], "unverified")

    async def test_rejects_wrong_turn_hash(self):
        response = {
            "response": "unrelated",
            "conversation_id": "cin-check",
            "request_content_hash": "different",
            "request_turn_id": "a" * 32,
            "runtime_payload": {
                "request_turn_id": "a" * 32, "request_content_hash": "different",
            },
        }
        with patch.object(server, "cin_bridge_http", new=AsyncMock(return_value=(200, response))):
            outcome = await server.cin_runtime_exchange("local hello", "cin-check")
        self.assertFalse(outcome["ok"])
        self.assertFalse(outcome["runtime_execution_verified"])


if __name__ == "__main__":
    unittest.main()
