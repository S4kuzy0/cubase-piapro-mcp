from mcp.server.mcpserver.exceptions import ToolError


class IntegrationError(ToolError):
    """An actionable error that is safe to return through MCP."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")
