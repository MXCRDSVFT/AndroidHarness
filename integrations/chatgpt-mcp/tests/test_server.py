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


if __name__ == "__main__":
    unittest.main()
