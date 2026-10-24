# AndroidHarness to ChatGPT MCP adapter

A constrained read-only adapter that exposes an SSH-accessible AndroidHarness workspace to ChatGPT through Model Context Protocol (MCP). This is a bridge to a configured SSH/SFTP workspace, not a direct call into AndroidHarness's private in-app tool registry.

## Security

- SSH key authentication with pinned known-hosts verification.
- SFTP-only; no shell command execution, Git mutation, process control, or device input.
- Workspace-relative paths only; traversal and symlink entries are rejected.
- Reads are bounded by ANDROIDHARNESS_MAX_READ_BYTES.
- Keep the MCP listener private until deployed behind HTTPS and an approved authentication gateway. Never expose it unauthenticated.
- This does not register itself in ChatGPT. The deployed endpoint must be added through ChatGPT's supported custom-app/MCP settings, subject to account/workspace eligibility.

## Setup

Requires Python 3.11+.

1. Create a virtual environment and install requirements: `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`.
2. Set ANDROIDHARNESS_SSH_HOST, ANDROIDHARNESS_SSH_USER, ANDROIDHARNESS_SSH_KEY, ANDROIDHARNESS_KNOWN_HOSTS and ANDROIDHARNESS_WORKSPACE (absolute remote root). Optional: ANDROIDHARNESS_MAX_READ_BYTES (default 200000).
3. Run `python server.py`. Local endpoint: `http://127.0.0.1:8000/mcp`.
4. Run `python -m unittest -v test_server.py`.
5. Deploy behind TLS/authentication or a supported secure tunnel, then connect that HTTPS MCP endpoint from ChatGPT on the web.

## Tools

- `workspace_list`: list a directory beneath the configured root.
- `workspace_read_file`: read bounded UTF-8 text beneath the configured root.

This is an initial read-only adapter slice. ChatGPT registration, endpoint deployment, and end-to-end SSH tests remain separate deployment steps.