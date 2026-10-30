"""uplus-mcp: MCP server for the QingSoft U+ course platform.

Modules
-------
config      Load and validate runtime configuration.
models      Data models and U+ status/question-type enums.
browser     CDP-attached Playwright session over the user's own browser.
platform    Read-only U+ REST API client (courses, homeworks, questions).
operator    Page-level UI operations: answer objective questions, submit, upload.
browse      Generic single-step and batched page actions for the attached browser.
worker      Dedicated thread that owns the Playwright sync API calls.
launcher    Locate or launch Edge/Chrome with the CDP debug port enabled.
solver      Answer normalization and structured question payloads for an LLM.
report      Markdown patrol report builder and persistence.
report_generator  Experiment report .docx builder (python-docx).
experiment  Experiment detail reading and report upload.
server      FastMCP server exposing the tools to MCP clients.
"""

__version__ = "0.7.0"
