"""Regression tests for AndroidHarness -> device-local CIN-Core conversation bridge."""
import hashlib
import unittest
from unittest.mock import AsyncMock, patch

import server


class CINRuntimeMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_turn_does_not_connect(self):
        with patch.object(server, "cin_bridge_http", new_callable=AsyncMock) as call:
            result = await server.cin_runtime_exchange("")
            self.assertFalse(result["ok"])
            call.assert_not_awaited()

    async def test_invalid_conversation_id_does_not_connect(self):
        with patch.object(server, "cin_bridge_http", new_callable=AsyncMock) as call:
            result = await server.cin_runtime_exchange("hello", "../other")
            self.assertFalse(result["ok"])
            call.assert_not_awaited()

    async def test_device_attested_response(self):
        content, turn = "hello CIN", "a" * 32
        request_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        payload = {
            "response": "Omega returned this",
            "conversation_id": "chatgpt-mcp",
            "request_content_hash": request_hash,
            "request_turn_id": turn,
            "runtime_payload": {"request_turn_id": turn, "request_content_hash": request_hash},
            "runtime_attestation_verified": True,
            "action_execution_verified": False,
        }
        with patch.object(server, "cin_bridge_http", new_callable=AsyncMock, return_value=(200, payload)) as call:
            result = await server.cin_runtime_exchange(content)
        self.assertTrue(result["ok"])
        self.assertTrue(result["runtime_execution_verified"])
        self.assertEqual(result["response"], "Omega returned this")
        self.assertEqual(call.await_args.args[0:2], ("POST", "/exchange"))
        self.assertEqual(call.await_args.args[2]["content"], content)

    async def test_response_without_attestation_is_not_verified(self):
        content, turn = "test", "b" * 32
        request_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        payload = {
            "response": "unverified response", "conversation_id": "chatgpt-mcp",
            "request_content_hash": request_hash, "request_turn_id": turn,
            "runtime_payload": {"request_turn_id": turn, "request_content_hash": request_hash,
                                "runtime_execution_verified": True},
            "runtime_execution_verified": True,
            "runtime_attestation_verified": False, "action_execution_verified": True,
        }
        with patch.object(server, "cin_bridge_http", new_callable=AsyncMock, return_value=(200, payload)):
            result = await server.cin_runtime_exchange(content)
        self.assertTrue(result["ok"])
        self.assertFalse(result["runtime_execution_verified"])
        self.assertFalse(result["action_execution_verified"])
        self.assertIsNone(result["execution_owner"])

    async def test_wrong_turn_cannot_be_reported_as_local(self):
        content = "test"
        payload = {
            "response": "wrong turn", "conversation_id": "chatgpt-mcp",
            "request_content_hash": "deadbeef" * 2,
            "request_turn_id": "c" * 32,
            "runtime_payload": {"request_turn_id": "c" * 32, "request_content_hash": "deadbeef" * 2},
            "runtime_attestation_verified": True,
        }
        with patch.object(server, "cin_bridge_http", new_callable=AsyncMock, return_value=(200, payload)):
            result = await server.cin_runtime_exchange(content)
        self.assertFalse(result["ok"])
        self.assertFalse(result["runtime_execution_verified"])

    async def test_device_timeout_remains_queued(self):
        with patch.object(server, "cin_bridge_http", new_callable=AsyncMock,
                          return_value=(504, {"queued": True, "request_turn_id": "d" * 32})):
            result = await server.cin_runtime_exchange("hello")
        self.assertFalse(result["ok"])
        self.assertTrue(result["queued"])
        self.assertFalse(result["runtime_execution_verified"])

    async def test_status_does_not_claim_turn_execution(self):
        with patch.object(server, "cin_bridge_http", new_callable=AsyncMock,
                          return_value=(200, {"bridge": "cin_omega83_conversation_bridge",
                                              "runtime_bridge_verified": True})):
            result = await server.cin_runtime_status()
        self.assertTrue(result["ok"])
        self.assertTrue(result["runtime_bridge_verified"])
        self.assertNotIn("runtime_execution_verified", result)


if __name__ == "__main__":
    unittest.main()
