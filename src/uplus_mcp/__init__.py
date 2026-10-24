"""uplus-mcp: MCP server for the QingSoft U+ course platform.

Modules
-------
config      Load and validate runtime configuration.
models      Data models and U+ status/question-type enums.
browser     Playwright browser session with persisted login state.
platform    Read-only U+ REST API client (courses, homeworks, questions).
operator    Page-level UI operations: answer objective questions, submit, upload.
solver      Answer normalization and structured question payloads for an LLM.
report      Markdown patrol report builder and persistence.
server      FastMCP server exposing the tools to MCP clients.
"""

__version__ = "0.1.0"
