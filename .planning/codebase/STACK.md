# Technology Stack

**Analysis Date:** 2026-03-21

## Languages

**Primary:**
- Python 3.13 - Core agent framework, backend services, playground implementations
- Python 3.10+ - Minimum supported version (as specified in pyproject.toml)

**Secondary:**
- JavaScript/Node.js - MCP (Model Context Protocol) servers via npx commands

## Runtime

**Environment:**
- Python 3.13 (`.python-version`)

**Package Manager:**
- uv (modern Python package manager)
- Lockfile: `uv.lock` (present and committed)
- Fallback: pip (used in Docker for initial setup)

## Frameworks

**Core:**
- FastAPI >=0.100.0 - Web framework for REST API and WebSocket server
- Uvicorn[standard] >=0.22.0 - ASGI server for FastAPI
- Gunicorn >=25.0.3 - Production WSGI HTTP server (with UvicornWorker adapter)

**Async/Concurrency:**
- asyncio (Python stdlib) - Event loop, coroutines, task management
- WebSockets >=11.0 - WebSocket protocol support

**LLM & AI:**
- openai - OpenAI API client (native support)
- anthropic - Anthropic/Claude API client (native support)
- google-genai - Google Generative AI API client (native support)
- mcp >=1.0 - Model Context Protocol for tool integration

**Data & Storage:**
- PyMySQL >=1.1.2 - MySQL database client (synchronous, cursor-based)
- redis >=5.0.0 - Redis client for caching and pub/sub
- oss2 - Aliyun OSS (Object Storage Service) client for file upload/download
- pydantic - Data validation and settings management
- PyYAML - YAML configuration parsing

**Web & HTTP:**
- httpx >=0.28.1 - Async HTTP client (used for Bohrium Open API calls)
- aiohttp - Async HTTP client/server framework
- requests - Synchronous HTTP client
- beautifulsoup4 - HTML parsing
- lxml - XML/HTML parsing
- paramiko >=4.0.0 - SSH client for remote execution

**Document & Media Processing:**
- python-docx >=1.2.0 - Word document generation
- PyMuPDF - PDF extraction and manipulation
- weasyprint - HTML to PDF conversion (system dependencies configured in Dockerfile)

**Scientific Computation (Optional):**
- ase >=3.22 - Atomic Simulation Environment
- pymatgen >=2024.1 - Materials science toolkit
- dpdata >=0.2.18 - Molecular data processing

**External Services:**
- bohrium-sdk >=0.15.0 - Bohrium cloud computing platform SDK (for job submission)
- bohrium-open-sdk >=1.0.0 - Bohrium Open API SDK (optional dependency)

**Configuration & Environment:**
- python-dotenv - Environment variable management (.env loading)

## Key Dependencies

**Critical:**
- openai, anthropic, google-genai - Multiple LLM provider support (pluggable via LLMConfig)
- FastAPI + Uvicorn - Web service foundation for chat API and streaming
- mcp - Tool integration protocol (filesystem, bing-search, mind-map, stock-analysis servers)
- PyMySQL - Session storage, chat history persistence
- redis - Multi-worker coordination (pub/sub for stop signals, session lifecycle)

**Infrastructure:**
- Bohrium SDK - Integration with cloud HPC/GPU infrastructure for computation jobs
- oss2 - Cloud file storage (mandatory for calculation MCP uploads)
- paramiko - SSH execution for remote operations
- httpx - HTTP requests to Bohrium Open API

## Configuration

**Environment:**
- `.env.template` - Template with all required variables (provided in repo)
- `.env.{SERVICE_ENV}` - Environment-specific overrides (test, uat, prod)
- Loading: `src/utils/constant.py` loads `.env` first, then `.env.{SERVICE_ENV}` to override

**Build:**
- `pyproject.toml` - Hatch-based build configuration with optional dependency groups
- `uv.lock` - Pinned versions for reproducible builds

**Logging:**
- Structured logging via `src/utils/logger.py` (JSON format, configurable levels)
- Configuration: `config.yaml` (logging section) specifies console/file output

**YAML Configuration:**
- `configs/config.yaml` - Unified LLM, agent, session, and env cluster settings
- `configs/mcp_config.json` - MCP server definitions (filesystem, bing-search, mind-map, stock-analysis)

## Platform Requirements

**Development:**
- Python 3.10+ virtual environment
- Git (for version control, pre-commit hooks)
- uv package manager (recommended) or pip

**Docker Container (Production):**
- Base image: `registry.dp.tech/public/python:3.13-slim`
- System dependencies: libpango, libharfbuzz, libffi, fontconfig, curl, wget, unzip, git, libjemalloc2
- Python environment: uv venv + uv sync (deterministic from uv.lock)
- Fonts: Noto Sans CJK (installed via /app/fonts/NotoSansCJK-*.ttc)

**Deployment:**
- Gunicorn with Uvicorn workers (see Dockerfile start.sh)
- Single worker mode (-w 1) by default for tracemalloc compatibility
- Optional LD_PRELOAD jemalloc for improved memory management
- Port: 80 (exposed in Dockerfile)

**Multi-container Setup:**
- API container: Dockerfile with default target=api (FastAPI + Gunicorn)
- Worker container: Dockerfile --target worker (agent job processing)
- MySQL: External database (configurable via MYSQL_* env vars)
- Redis: Optional, enables cross-worker session coordination

---

*Stack analysis: 2026-03-21*
