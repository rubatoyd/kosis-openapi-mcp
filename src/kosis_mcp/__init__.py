"""kosis-openapi-mcp — KOSIS 공유서비스(OpenAPI) MCP 서버 + CLI."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("kosis-openapi-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
