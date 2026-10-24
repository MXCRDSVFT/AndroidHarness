# AndroidHarness to ChatGPT MCP adapter

A constrained MCP bridge that exposes a configured AndroidHarness workspace to ChatGPT over SSH/SFTP. It provides bounded file operations and fixed, read-only Git inspection.

## Security

- SSH key authentication with pinned known-hosts verification.
- The only command execution is fixed, read-only `git status` and `git diff`; no arbitrary shell, Git mutation, process control, or device input.
- Workspace-relative paths only; traversal and symlink entries are rejected.
- Reads are bounded by ANDROIDHARNESS_MAX_READ_BYTES.
- Keep the MCP listener private until deployed behind HTTPS and an approved authentication gateway. Never expose it unauthenticated.
- This does not register itself in ChatGPT. The deployed endpoint must be added through ChatGPT's supported custom-app/MCP settings, subject to account/workspace eligibility.

## Setup

Requires Python 3.11+.

1. Create a virtual environment and install requirements: `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`.
2. Set ANDROIDHARNESS_SSH_HOST, ANDROIDHARNESS_SSH_USER, ANDROIDHARNESS_SSH_KEY, ANDROIDHARNESS_KNOWN_HOSTS and ANDROIDHARNESS_WORKSPACE (absolute remote root). Optional: ANDROIDHARNESS_MAX_READ_BYTES (default 200000).
3. Run the local checks with `python -m unittest discover -s tests -v`. Then start the server with `python server.py`. Local endpoint: `http://127.0.0.1:8000/mcp`.
4. Deploy behind TLS/authentication or a supported secure tunnel, then connect that HTTPS MCP endpoint from ChatGPT on the web.

## Tools

- `workspace_list`: list a directory beneath the configured root.
- `workspace_read_file`: read bounded UTF-8 text beneath the configured root.
- `workspace_write_file`: write bounded UTF-8 text inside the workspace using a temporary file and atomic SFTP POSIX rename; symlink targets are rejected.
- `git_status`: read-only status for the configured workspace.
- `git_diff`: bounded, read-only diff for tracked changes.

The adapter exposes a deliberately limited workspace tool subset over SSH; it does not invoke AndroidHarness's in-process Kotlin `ToolRegistry`. Unit tests cover path validation, workspace boundaries, symlink rejection, and write-input limits. Deploy only behind authenticated HTTPS or a supported secure tunnel, then register the MCP endpoint in an eligible ChatGPT account/workspace.
