"""PyInstaller entry point for the standalone knx-ets-mcp.exe.

Bundles the Python runtime so the MCP server runs on a plain Windows machine
without a separate Python installation. Built in CI (see .github/workflows/release.yml).
"""

from knx_ets_mcp.server import main

if __name__ == "__main__":
    main()
