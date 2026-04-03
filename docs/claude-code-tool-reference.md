# Claude Code Tool Reference

Claude Code 工具体系参考文档。记录每个与 matmaster 存在对应关系的工具，包含组装完成后发送给 Claude API 的完整 `name`、`description`、`input_schema`。

> 数据来源：claude-code 源码 (`tools/*/prompt.ts` + Zod inputSchema)
> 组装流程：`tool.prompt(ctx)` → description, `zodToJsonSchema(tool.inputSchema)` → input_schema

## 目录

| # | Claude-Code Tool | MatMaster Tool | 核心差异 |
|---|---|---|---|
| 1 | [Read](#1-read) | `read_file` | CC 多 `pages` (PDF分页) |
| 2 | [Edit](#2-edit) | `edit_file` | CC 多 `replace_all` (全局替换) |
| 3 | [Write](#3-write) | `write_file` | 基本一致 |
| 4 | [Bash](#4-bash) | `execute_bash` | CC 多 `run_in_background`, `description`, `dangerouslyDisableSandbox` |
| 5 | [Glob](#5-glob) | `glob` | 基本一致 |
| 6 | [Grep](#6-grep) | `grep` | CC 多 `output_mode`, `-A/-B/-C`, `multiline`, `head_limit`, `offset`, `type` |
| 7 | [WebFetch](#7-webfetch) | `web_fetch` | CC 用单 URL + prompt；MM 用 URL 数组无 prompt |
| 8 | [WebSearch](#8-websearch) | `mm_web_search` | CC 多 domain 过滤；MM 多 `top_k`, `gl`, `hl` |
| 9 | [Agent](#9-agent) | `spawn` | CC 多 `model`, `run_in_background`, `isolation`, `subagent_type` |
| 10 | [TodoWrite](#10-todowrite) | `task_create/list/get/update/complete` | 架构差异：CC 单工具原子替换 vs MM 五工具 CRUD |
| 11 | [ToolSearch](#11-toolsearch) | — | CC 独有，延迟加载工具的发现/激活机制 |
| 12 | [Skill](#12-skill) | — | CC 独有，slash command 技能执行引擎 |
| 13 | [AskUserQuestion](#13-askuserquestion) | — | CC 独有，结构化多选问答（含 preview） |
| 14 | [SendMessage](#14-sendmessage) | — | CC 独有，Agent 间 / 跨会话消息通信 |

---

## 组装流程

```
Tool 定义 (TypeScript)
  │
  ├── tool.prompt(ctx)                    → description (完整使用说明)
  │     可动态注入：当前日期、feature flag、agent 列表等
  │
  ├── zodToJsonSchema(tool.inputSchema)   → input_schema (JSON Schema)
  │     Zod v4 toJSONSchema() + WeakMap 缓存
  │
  └── 组装为 BetaTool 对象
        { name, description, input_schema, ?strict, ?cache_control, ?defer_loading }
        └── 发送到 Claude API tools[] 数组
```

关键设计特点：

- description 极其详细，是完整使用手册（Usage、注意事项、禁止行为），直接影响模型行为
- schema 使用 `additionalProperties: false` 严格模式
- description 可动态生成（Bash 根据 sandbox 配置、WebSearch 注入当前年月、Agent 注入可用类型列表）
- 参数 description 承担双重职责：类型文档 + 行为约束

---

## 1. Read

**对应 matmaster 工具：** `read_file`

**差异：** CC 多 `pages` 参数支持 PDF 分页读取

```json
{
  "name": "Read",
  "description": "Reads a file from the local filesystem. You can access any file directly by using this tool.\nAssume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.\n\nUsage:\n- The file_path parameter must be an absolute path, not a relative path\n- By default, it reads up to 2000 lines starting from the beginning of the file\n- When you already know which part of the file you need, only read that part. This can be important for larger files.\n- Results are returned using cat -n format, with line numbers starting at 1\n- This tool allows Claude Code to read images (eg PNG, JPG, etc). When reading an image file the contents are presented visually as Claude Code is a multimodal LLM.\n- This tool can read PDF files (.pdf). For large PDFs (more than 10 pages), you MUST provide the pages parameter to read specific page ranges (e.g., pages: \"1-5\"). Reading a large PDF without the pages parameter will fail. Maximum 20 pages per request.\n- This tool can read Jupyter notebooks (.ipynb files) and returns all cells with their outputs, combining code, text, and visualizations.\n- This tool can only read files, not directories. To read a directory, use an ls command via the Bash tool.\n- You will regularly be asked to read screenshots. If the user provides a path to a screenshot, ALWAYS use this tool to view the file at the path. This tool will work with all temporary file paths.\n- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents.",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "file_path": {
        "type": "string",
        "description": "The absolute path to the file to read"
      },
      "offset": {
        "type": "number",
        "description": "The line number to start reading from. Only provide if the file is too large to read at once"
      },
      "limit": {
        "type": "number",
        "description": "The number of lines to read. Only provide if the file is too large to read at once."
      },
      "pages": {
        "type": "string",
        "description": "Page range for PDF files (e.g., \"1-5\", \"3\", \"10-20\"). Only applicable to PDF files. Maximum 20 pages per request."
      }
    },
    "required": ["file_path"],
    "additionalProperties": false
  }
}
```

---

## 2. Edit

**对应 matmaster 工具：** `edit_file`

**差异：** CC 多 `replace_all` 参数支持全局替换

```json
{
  "name": "Edit",
  "description": "Performs exact string replacements in files.\n\nUsage:\n- You must use your `Read` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file. \n- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: line number + tab. Everything after that is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.\n- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.\n- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.\n- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.\n- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance.",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "file_path": {
        "type": "string",
        "description": "The absolute path to the file to modify"
      },
      "old_string": {
        "type": "string",
        "description": "The text to replace"
      },
      "new_string": {
        "type": "string",
        "description": "The text to replace it with (must be different from old_string)"
      },
      "replace_all": {
        "type": "boolean",
        "default": false,
        "description": "Replace all occurrences of old_string (default false)"
      }
    },
    "required": ["file_path", "old_string", "new_string"],
    "additionalProperties": false
  }
}
```

---

## 3. Write

**对应 matmaster 工具：** `write_file`

**差异：** 基本一致

```json
{
  "name": "Write",
  "description": "Writes a file to the local filesystem.\n\nUsage:\n- This tool will overwrite the existing file if there is one at the provided path.\n- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.\n- Prefer the Edit tool for modifying existing files — it only sends the diff. Only use this tool to create new files or for complete rewrites.\n- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.\n- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked.",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "file_path": {
        "type": "string",
        "description": "The absolute path to the file to write (must be absolute, not relative)"
      },
      "content": {
        "type": "string",
        "description": "The content to write to the file"
      }
    },
    "required": ["file_path", "content"],
    "additionalProperties": false
  }
}
```

---

## 4. Bash

**对应 matmaster 工具：** `execute_bash`

**差异：** CC 多 `run_in_background`、`description`、`dangerouslyDisableSandbox`；description 极长且包含 git/PR 操作指南

```json
{
  "name": "Bash",
  "description": "Executes a given bash command and returns its output.\n\nThe working directory persists between commands, but shell state does not. The shell environment is initialized from the user's profile (bash or zsh).\n\nIMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:\n\n - File search: Use Glob (NOT find or ls)\n - Content search: Use Grep (NOT grep or rg)\n - Read files: Use Read (NOT cat/head/tail)\n - Edit files: Use Edit (NOT sed/awk)\n - Write files: Use Write (NOT echo >/cat <<EOF)\n - Communication: Output text directly (NOT echo/printf)\nWhile the Bash tool can do similar things, it's better to use the built-in tools as they provide a better user experience and make it easier to review tool calls and give permission.\n\n# Instructions\n - If your command will create new directories or files, first use this tool to run `ls` to verify the parent directory exists and is the correct location.\n - Always quote file paths that contain spaces with double quotes in your command (e.g., cd \"path with spaces/file.txt\")\n - Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.\n - You may specify an optional timeout in milliseconds (up to 600000ms / 10 minutes). By default, your command will timeout after 120000ms (2 minutes).\n - You can use the `run_in_background` parameter to run the command in the background. Only use this if you don't need the result immediately and are OK being notified when the command completes later. You do not need to check the output right away - you'll be notified when it finishes. You do not need to use '&' at the end of the command when using this parameter.\n - When issuing multiple commands:\n  - If the commands are independent and can run in parallel, make multiple Bash tool calls in a single message.\n  - If the commands depend on each other and must run sequentially, use a single Bash call with '&&' to chain them together.\n  - Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.\n  - DO NOT use newlines to separate commands (newlines are ok in quoted strings).\n - For git commands:\n  - Prefer to create a new commit rather than amending an existing commit.\n  - Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), consider whether there is a safer alternative that achieves the same goal. Only use destructive operations when they are truly the best approach.\n  - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) unless the user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue.\n - Avoid unnecessary `sleep` commands:\n  - Do not sleep between commands that can run immediately — just run them.\n  - If your command is long running and you would like to be notified when it finishes — use `run_in_background`. No sleep needed.\n  - Do not retry failing commands in a sleep loop — diagnose the root cause.\n  - If waiting for a background task you started with `run_in_background`, you will be notified when it completes — do not poll.\n  - If you must poll an external process, use a check command (e.g. `gh run view`) rather than sleeping first.\n  - If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user.",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "command": {
        "type": "string",
        "description": "The command to execute"
      },
      "timeout": {
        "type": "number",
        "description": "Optional timeout in milliseconds (max 600000)"
      },
      "run_in_background": {
        "type": "boolean",
        "description": "Set to true to run this command in the background. Use Read to read the output later."
      },
      "description": {
        "type": "string",
        "description": "Clear, concise description of what this command does in active voice. Never use words like \"complex\" or \"risk\" in the description - just describe what it does.\n\nFor simple commands (git, npm, standard CLI tools), keep it brief (5-10 words):\n- ls → \"List files in current directory\"\n- git status → \"Show working tree status\"\n- npm install → \"Install package dependencies\"\n\nFor commands that are harder to parse at a glance (piped commands, obscure flags, etc.), add enough context to clarify what it does:\n- find . -name \"*.tmp\" -exec rm {} \\; → \"Find and delete all .tmp files recursively\"\n- git reset --hard origin/main → \"Discard all local changes and match remote main\"\n- curl -s url | jq '.data[]' → \"Fetch JSON from URL and extract data array elements\""
      },
      "dangerouslyDisableSandbox": {
        "type": "boolean",
        "description": "Set this to true to dangerously override sandbox mode and run commands without sandboxing."
      }
    },
    "required": ["command"],
    "additionalProperties": false
  }
}
```

---

## 5. Glob

**对应 matmaster 工具：** `glob`

**差异：** 基本一致

```json
{
  "name": "Glob",
  "description": "- Fast file pattern matching tool that works with any codebase size\n- Supports glob patterns like \"**/*.js\" or \"src/**/*.ts\"\n- Returns matching file paths sorted by modification time\n- Use this tool when you need to find files by name patterns\n- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "pattern": {
        "type": "string",
        "description": "The glob pattern to match files against"
      },
      "path": {
        "type": "string",
        "description": "The directory to search in. If not specified, the current working directory will be used. IMPORTANT: Omit this field to use the default directory. DO NOT enter \"undefined\" or \"null\" - simply omit it for the default behavior. Must be a valid directory path if provided."
      }
    },
    "required": ["pattern"],
    "additionalProperties": false
  }
}
```

---

## 6. Grep

**对应 matmaster 工具：** `grep`

**差异：** CC 参数丰富得多——`output_mode`、`-A/-B/-C` 上下文行、`multiline`、`head_limit`、`offset`、`type` 文件类型过滤

```json
{
  "name": "Grep",
  "description": "A powerful search tool built on ripgrep\n\n  Usage:\n  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.\n  - Supports full regex syntax (e.g., \"log.*Error\", \"function\\s+\\w+\")\n  - Filter files with glob parameter (e.g., \"*.js\", \"**/*.tsx\") or type parameter (e.g., \"js\", \"py\", \"rust\")\n  - Output modes: \"content\" shows matching lines, \"files_with_matches\" shows only file paths (default), \"count\" shows match counts\n  - Use Agent tool for open-ended searches requiring multiple rounds\n  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)\n  - Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "pattern": {
        "type": "string",
        "description": "The regular expression pattern to search for in file contents"
      },
      "path": {
        "type": "string",
        "description": "File or directory to search in (rg PATH). Defaults to current working directory."
      },
      "glob": {
        "type": "string",
        "description": "Glob pattern to filter files (e.g. \"*.js\", \"*.{ts,tsx}\") - maps to rg --glob"
      },
      "output_mode": {
        "type": "string",
        "enum": ["content", "files_with_matches", "count"],
        "description": "Output mode: \"content\" shows matching lines (supports -A/-B/-C context, -n line numbers, head_limit), \"files_with_matches\" shows file paths (supports head_limit), \"count\" shows match counts (supports head_limit). Defaults to \"files_with_matches\"."
      },
      "-B": {
        "type": "number",
        "description": "Number of lines to show before each match (rg -B). Requires output_mode: \"content\", ignored otherwise."
      },
      "-A": {
        "type": "number",
        "description": "Number of lines to show after each match (rg -A). Requires output_mode: \"content\", ignored otherwise."
      },
      "-C": {
        "type": "number",
        "description": "Alias for context."
      },
      "context": {
        "type": "number",
        "description": "Number of lines to show before and after each match (rg -C). Requires output_mode: \"content\", ignored otherwise."
      },
      "-n": {
        "type": "boolean",
        "description": "Show line numbers in output (rg -n). Requires output_mode: \"content\", ignored otherwise. Defaults to true."
      },
      "-i": {
        "type": "boolean",
        "description": "Case insensitive search (rg -i)"
      },
      "type": {
        "type": "string",
        "description": "File type to search (rg --type). Common types: js, py, rust, go, java, etc. More efficient than include for standard file types."
      },
      "head_limit": {
        "type": "number",
        "description": "Limit output to first N lines/entries, equivalent to \"| head -N\". Works across all output modes: content (limits output lines), files_with_matches (limits file paths), count (limits count entries). Defaults to 250 when unspecified. Pass 0 for unlimited (use sparingly — large result sets waste context)."
      },
      "offset": {
        "type": "number",
        "description": "Skip first N lines/entries before applying head_limit, equivalent to \"| tail -n +N | head -N\". Works across all output modes. Defaults to 0."
      },
      "multiline": {
        "type": "boolean",
        "description": "Enable multiline mode where . matches newlines and patterns can span lines (rg -U --multiline-dotall). Default: false."
      }
    },
    "required": ["pattern"],
    "additionalProperties": false
  }
}
```

---

## 7. WebFetch

**对应 matmaster 工具：** `web_fetch`

**差异：** CC 接受单个 URL + prompt（由小模型处理内容）；MM 接受 URL 数组，无 prompt，直接返回原始内容

```json
{
  "name": "WebFetch",
  "description": "IMPORTANT: WebFetch WILL FAIL for authenticated or private URLs. Before using this tool, check if the URL points to an authenticated service (e.g. Google Docs, Confluence, Jira, GitHub). If so, look for a specialized MCP tool that provides authenticated access.\n\n- Fetches content from a specified URL and processes it using an AI model\n- Takes a URL and a prompt as input\n- Fetches the URL content, converts HTML to markdown\n- Processes the content with the prompt using a small, fast model\n- Returns the model's response about the content\n- Use this tool when you need to retrieve and analyze web content\n\nUsage notes:\n  - IMPORTANT: If an MCP-provided web fetch tool is available, prefer using that tool instead of this one, as it may have fewer restrictions.\n  - The URL must be a fully-formed valid URL\n  - HTTP URLs will be automatically upgraded to HTTPS\n  - The prompt should describe what information you want to extract from the page\n  - This tool is read-only and does not modify any files\n  - Results may be summarized if the content is very large\n  - Includes a self-cleaning 15-minute cache for faster responses when repeatedly accessing the same URL\n  - When a URL redirects to a different host, the tool will inform you and provide the redirect URL in a special format. You should then make a new WebFetch request with the redirect URL to fetch the content.\n  - For GitHub URLs, prefer using the gh CLI via Bash instead (e.g., gh pr view, gh issue view, gh api).",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "url": {
        "type": "string",
        "format": "uri",
        "description": "The URL to fetch content from"
      },
      "prompt": {
        "type": "string",
        "description": "The prompt to run on the fetched content"
      }
    },
    "required": ["url", "prompt"],
    "additionalProperties": false
  }
}
```

---

## 8. WebSearch

**对应 matmaster 工具：** `mm_web_search`

**差异：** CC 多 `allowed_domains`/`blocked_domains` 域名过滤；MM 多 `top_k`、`gl`、`hl` 参数；description 动态注入当前年月

```json
{
  "name": "WebSearch",
  "description": "- Allows Claude to search the web and use the results to inform responses\n- Provides up-to-date information for current events and recent data\n- Returns search result information formatted as search result blocks, including links as markdown hyperlinks\n- Use this tool for accessing information beyond Claude's knowledge cutoff\n- Searches are performed automatically within a single API call\n\nCRITICAL REQUIREMENT - You MUST follow this:\n  - After answering the user's question, you MUST include a \"Sources:\" section at the end of your response\n  - In the Sources section, list all relevant URLs from the search results as markdown hyperlinks: [Title](URL)\n  - This is MANDATORY - never skip including sources in your response\n  - Example format:\n\n    [Your answer here]\n\n    Sources:\n    - [Source Title 1](https://example.com/1)\n    - [Source Title 2](https://example.com/2)\n\nUsage notes:\n  - Domain filtering is supported to include or block specific websites\n  - Web search is only available in the US\n\nIMPORTANT - Use the correct year in search queries:\n  - The current month is ${currentMonth} ${currentYear}. You MUST use this year when searching for recent information, documentation, or current events.\n  - Example: If the user asks for \"latest React docs\", search for \"React documentation\" with the current year, NOT last year",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "minLength": 2,
        "description": "The search query to use"
      },
      "allowed_domains": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Only include search results from these domains"
      },
      "blocked_domains": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Never include search results from these domains"
      }
    },
    "required": ["query"],
    "additionalProperties": false
  }
}
```

---

## 9. Agent

**对应 matmaster 工具：** `spawn`

**差异：** CC 支持 `subagent_type` 选择预定义 agent 类型、`model` 覆盖、`run_in_background` 后台运行、`isolation` worktree 隔离；MM 用 `exp_name` 选择 TOML 定义的 exp

```json
{
  "name": "Agent",
  "description": "Launch a new agent to handle complex, multi-step tasks autonomously.\n\nThe Agent tool launches specialized agents (subprocesses) that autonomously handle complex tasks. Each agent type has specific capabilities and tools available to it.\n\nAvailable agent types and the tools they have access to:\n- general-purpose: General-purpose agent for researching complex questions, searching for code, and executing multi-step tasks. (Tools: *)\n- Explore: Fast agent specialized for exploring codebases. Use this when you need to quickly find files by patterns, search code for keywords, or answer questions about the codebase. (Tools: All tools except Agent, Edit, Write, NotebookEdit)\n- Plan: Software architect agent for designing implementation plans. (Tools: All tools except Agent, Edit, Write, NotebookEdit)\n\nWhen using the Agent tool, specify a subagent_type parameter to select which agent type to use. If omitted, the general-purpose agent is used.\n\nWhen NOT to use the Agent tool:\n- If you want to read a specific file path, use the Read tool or the Glob tool instead of the Agent tool, to find the match more quickly\n- If you are searching for a specific class definition like \"class Foo\", use the Glob tool instead, to find the match more quickly\n- If you are searching for code within a specific file or set of 2-3 files, use the Read tool instead of the Agent tool, to find the match more quickly\n- Other tasks that are not related to the agent descriptions above\n\nUsage notes:\n- Always include a short description (3-5 words) summarizing what the agent will do\n- Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a single message with multiple tool uses\n- When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.\n- Provide clear, detailed prompts so the agent can work autonomously and return exactly the information you need.\n- The agent's outputs should generally be trusted",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "description": {
        "type": "string",
        "description": "A short (3-5 word) description of the task"
      },
      "prompt": {
        "type": "string",
        "description": "The task for the agent to perform"
      },
      "subagent_type": {
        "type": "string",
        "description": "The type of specialized agent to use for this task"
      },
      "model": {
        "type": "string",
        "enum": ["sonnet", "opus", "haiku"],
        "description": "Optional model override for this agent. Takes precedence over the agent definition's model frontmatter. If omitted, uses the agent definition's model, or inherits from the parent."
      },
      "run_in_background": {
        "type": "boolean",
        "description": "Set to true to run this agent in the background. You will be notified when it completes."
      },
      "isolation": {
        "type": "string",
        "enum": ["worktree"],
        "description": "Isolation mode. \"worktree\" creates a temporary git worktree so the agent works on an isolated copy of the repo."
      }
    },
    "required": ["description", "prompt"],
    "additionalProperties": false
  }
}
```

---

## 10. TodoWrite

**对应 matmaster 工具：** `task_create` / `task_list` / `task_get` / `task_update` / `task_complete`

**架构差异：** CC 使用单一 TodoWrite 工具，每次调用传入完整的 todo 列表（原子替换语义）；MM 使用五个独立工具实现 CRUD 操作，支持父任务-子任务层级

```json
{
  "name": "TodoWrite",
  "description": "Use this tool to create and manage a structured task list for your current coding session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.\nIt also helps the user understand the progress of the task and overall progress of their requests.\n\n## When to Use This Tool\nUse this tool proactively in these scenarios:\n\n1. Complex multi-step tasks - When a task requires 3 or more distinct steps or actions\n2. Non-trivial and complex tasks - Tasks that require careful planning or multiple operations\n3. User explicitly requests todo list - When the user directly asks you to use the todo list\n4. User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)\n5. After receiving new instructions - Immediately capture user requirements as todos\n6. When you start working on a task - Mark it as in_progress BEFORE beginning work. Ideally you should only have one todo as in_progress at a time\n7. After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation\n\n## When NOT to Use This Tool\n\nSkip using this tool when:\n1. There is only a single, straightforward task\n2. The task is trivial and tracking it provides no organizational benefit\n3. The task can be completed in less than 3 trivial steps\n4. The task is purely conversational or informational\n\nNOTE that you should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.\n\n## Task States and Management\n\n1. **Task States**: Use these states to track progress:\n   - pending: Task not yet started\n   - in_progress: Currently working on (limit to ONE task at a time)\n   - completed: Task finished successfully\n\n   **IMPORTANT**: Task descriptions must have two forms:\n   - content: The imperative form describing what needs to be done (e.g., \"Run tests\", \"Build the project\")\n   - activeForm: The present continuous form shown during execution (e.g., \"Running tests\", \"Building the project\")\n\n2. **Task Management**:\n   - Update task status in real-time as you work\n   - Mark tasks complete IMMEDIATELY after finishing (don't batch completions)\n   - Exactly ONE task must be in_progress at any time (not less, not more)\n   - Complete current tasks before starting new ones\n   - Remove tasks that are no longer relevant from the list entirely\n\n3. **Task Completion Requirements**:\n   - ONLY mark a task as completed when you have FULLY accomplished it\n   - If you encounter errors, blockers, or cannot finish, keep the task as in_progress\n   - When blocked, create a new task describing what needs to be resolved\n   - Never mark a task as completed if:\n     - Tests are failing\n     - Implementation is partial\n     - You encountered unresolved errors\n     - You couldn't find necessary files or dependencies",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "todos": {
        "type": "array",
        "description": "The updated todo list",
        "items": {
          "type": "object",
          "properties": {
            "content": {
              "type": "string",
              "minLength": 1
            },
            "status": {
              "type": "string",
              "enum": ["pending", "in_progress", "completed"]
            },
            "activeForm": {
              "type": "string",
              "minLength": 1
            }
          },
          "required": ["content", "status", "activeForm"],
          "additionalProperties": false
        }
      }
    },
    "required": ["todos"],
    "additionalProperties": false
  }
}
```

---

## 11. ToolSearch

**MatMaster 无对应工具**

CC 独有的延迟加载机制。当工具数量过多时（尤其 MCP 工具），大部分工具标记为 `defer_loading: true`，模型只能看到工具名称而看不到 schema。需要通过 ToolSearch 按关键字搜索或按名称选择，返回完整 schema 后才能调用。

```json
{
  "name": "ToolSearch",
  "description": "Fetches full schema definitions for deferred tools so they can be called.\n\nDeferred tools appear by name in <system-reminder> messages. Until fetched, only the name is known — there is no parameter schema, so the tool cannot be invoked. This tool takes a query, matches it against the deferred tool list, and returns the matched tools' complete JSONSchema definitions inside a <functions> block. Once a tool's schema appears in that result, it is callable exactly like any tool defined at the top of the prompt.\n\nResult format: each matched tool appears as one <function>{\"description\": \"...\", \"name\": \"...\", \"parameters\": {...}}</function> line inside the <functions> block — the same encoding as the tool list at the top of this prompt.\n\nQuery forms:\n- \"select:Read,Edit,Grep\" — fetch these exact tools by name\n- \"notebook jupyter\" — keyword search, up to max_results best matches\n- \"+slack send\" — require \"slack\" in the name, rank by remaining terms",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Query to find deferred tools. Use \"select:<tool_name>\" for direct selection, or keywords to search."
      },
      "max_results": {
        "type": "number",
        "default": 5,
        "description": "Maximum number of results to return (default: 5)"
      }
    },
    "required": ["query"],
    "additionalProperties": false
  }
}
```

**设计要点：**
- 返回格式是 `tool_reference` block，API 层面的特殊内容块类型
- 查询支持三种形式：`select:名称` 精确选择、关键字搜索、`+前缀 关键字` 要求名称包含前缀
- `shouldDefer` 未设置（ToolSearch 自身不被延迟，始终可用）
- `isReadOnly: true`，`isConcurrencySafe: true`

---

## 12. Skill

**MatMaster 无对应工具**

Skill 是 CC 的 slash command 执行引擎。用户通过 `/commit`、`/review-pr` 等触发，本质上是加载磁盘上的 skill 定义文件（含 prompt 和配置），将其内容注入当前对话上下文。

```json
{
  "name": "Skill",
  "description": "Execute a skill within the main conversation\n\nWhen users ask you to perform tasks, check if any of the available skills match. Skills provide specialized capabilities and domain knowledge.\n\nWhen users reference a \"slash command\" or \"/<something>\" (e.g., \"/commit\", \"/review-pr\"), they are referring to a skill. Use this tool to invoke it.\n\nHow to invoke:\n- Use this tool with the skill name and optional arguments\n- Examples:\n  - `skill: \"pdf\"` - invoke the pdf skill\n  - `skill: \"commit\", args: \"-m 'Fix bug'\"` - invoke with arguments\n  - `skill: \"review-pr\", args: \"123\"` - invoke with arguments\n  - `skill: \"ms-office-suite:pdf\"` - invoke using fully qualified name\n\nImportant:\n- Available skills are listed in system-reminder messages in the conversation\n- When a skill matches the user's request, this is a BLOCKING REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other response about the task\n- NEVER mention a skill without actually calling this tool\n- Do not invoke a skill that is already running\n- Do not use this tool for built-in CLI commands (like /help, /clear, etc.)\n- If you see a <command-name> tag in the current conversation turn, the skill has ALREADY been loaded - follow the instructions directly instead of calling this tool again",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "skill": {
        "type": "string",
        "description": "The skill name. E.g., \"commit\", \"review-pr\", or \"pdf\""
      },
      "args": {
        "type": "string",
        "description": "Optional arguments for the skill"
      }
    },
    "required": ["skill"],
    "additionalProperties": false
  }
}
```

**设计要点：**
- 执行模式分两种：`inline`（在当前上下文展开 skill 内容）和 `forked`（派生子 agent 执行）
- inline 模式的输出包含 `allowedTools` 字段，可限制后续调用只使用特定工具
- forked 模式的输出包含 `agentId` 和执行结果
- 支持 `namespace:skill` 完全限定名（如 `ms-office-suite:pdf`）
- `searchHint: "invoke a slash-command skill"`，用于 ToolSearch 发现

---

## 13. AskUserQuestion

**MatMaster 无对应工具**

结构化的多选问答工具。与在 description 里靠自然语言提问不同，AskUserQuestion 渲染成真正的 UI 组件（选项卡片、preview 面板），用户通过点击选择。

```json
{
  "name": "AskUserQuestion",
  "description": "Use this tool when you need to ask the user questions during execution. This allows you to:\n1. Gather user preferences or requirements\n2. Clarify ambiguous instructions\n3. Get decisions on implementation choices as you work\n4. Offer choices to the user about what direction to take.\n\nUsage notes:\n- Users will always be able to select \"Other\" to provide custom text input\n- Use multiSelect: true to allow multiple answers to be selected for a question\n- If you recommend a specific option, make that the first option in the list and add \"(Recommended)\" at the end of the label\n\nPlan mode note: In plan mode, use this tool to clarify requirements or choose between approaches BEFORE finalizing your plan. Do NOT use this tool to ask \"Is my plan ready?\" or \"Should I proceed?\" - use ExitPlanMode for plan approval. IMPORTANT: Do not reference \"the plan\" in your questions (e.g., \"Do you have feedback about the plan?\", \"Does the plan look good?\") because the user cannot see the plan in the UI until you call ExitPlanMode. If you need plan approval, use ExitPlanMode instead.\n\nPreview feature:\nUse the optional `preview` field on options when presenting concrete artifacts that users need to visually compare:\n- ASCII mockups of UI layouts or components\n- Code snippets showing different implementations\n- Diagram variations\n- Configuration examples\n\nPreview content is rendered as markdown in a monospace box. Multi-line text with newlines is supported. When any option has a preview, the UI switches to a side-by-side layout with a vertical option list on the left and preview on the right. Do not use previews for simple preference questions where labels and descriptions suffice. Note: previews are only supported for single-select questions (not multiSelect).",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "questions": {
        "type": "array",
        "minItems": 1,
        "maxItems": 4,
        "description": "Questions to ask the user (1-4 questions)",
        "items": {
          "type": "object",
          "properties": {
            "question": {
              "type": "string",
              "description": "The complete question to ask the user. Should be clear, specific, and end with a question mark."
            },
            "header": {
              "type": "string",
              "description": "Very short label displayed as a chip/tag (max 12 chars). Examples: \"Auth method\", \"Library\", \"Approach\"."
            },
            "options": {
              "type": "array",
              "minItems": 2,
              "maxItems": 4,
              "description": "The available choices for this question. Must have 2-4 options. There should be no \"Other\" option, that will be provided automatically.",
              "items": {
                "type": "object",
                "properties": {
                  "label": {
                    "type": "string",
                    "description": "The display text for this option (1-5 words)."
                  },
                  "description": {
                    "type": "string",
                    "description": "Explanation of what this option means or what will happen if chosen."
                  },
                  "preview": {
                    "type": "string",
                    "description": "Optional preview content rendered when this option is focused. Use for mockups, code snippets, or visual comparisons."
                  }
                },
                "required": ["label", "description"],
                "additionalProperties": false
              }
            },
            "multiSelect": {
              "type": "boolean",
              "default": false,
              "description": "Set to true to allow the user to select multiple options instead of just one."
            }
          },
          "required": ["question", "header", "options"],
          "additionalProperties": false
        }
      },
      "answers": {
        "type": "object",
        "description": "User answers collected by the permission component",
        "additionalProperties": { "type": "string" }
      },
      "annotations": {
        "type": "object",
        "description": "Optional per-question annotations from the user (e.g., notes on preview selections). Keyed by question text.",
        "additionalProperties": {
          "type": "object",
          "properties": {
            "preview": { "type": "string" },
            "notes": { "type": "string" }
          }
        }
      },
      "metadata": {
        "type": "object",
        "description": "Optional metadata for tracking and analytics purposes.",
        "properties": {
          "source": { "type": "string", "description": "Optional identifier for the source of this question." }
        }
      }
    },
    "required": ["questions"],
    "additionalProperties": false
  }
}
```

**设计要点：**
- `shouldDefer: true` — 工具执行被挂起，等待用户在 UI 中选择后恢复
- `answers` 和 `annotations` 字段由 UI 组件填充后回传，模型首次调用时不需要提供
- 每个问题 2-4 个选项，UI 自动追加 Other 选项（用户自由输入）
- `preview` 触发侧边栏布局：左侧选项列表 + 右侧 preview 面板
- Zod 层有 `.refine()` 校验：问题文本不能重复，同一问题内 label 不能重复

---

## 14. SendMessage

**MatMaster 无对应工具**

Agent 间消息通信工具。用于多 agent 协作场景（Agent Swarms），支持点对点消息、广播、以及跨会话通信。

```json
{
  "name": "SendMessage",
  "description": "Send a message to another agent.\n\n```json\n{\"to\": \"researcher\", \"summary\": \"assign task 1\", \"message\": \"start on task #1\"}\n```\n\n| `to` | |\n|---|---|\n| `\"researcher\"` | Teammate by name |\n| `\"*\"` | Broadcast to all teammates — expensive (linear in team size), use only when everyone genuinely needs it |\n\nYour plain text output is NOT visible to other agents — to communicate, you MUST call this tool. Messages from teammates are delivered automatically; you don't check an inbox. Refer to teammates by name, never by UUID. When relaying, don't quote the original — it's already rendered to the user.\n\n## Protocol responses (legacy)\n\nIf you receive a JSON message with `type: \"shutdown_request\"` or `type: \"plan_approval_request\"`, respond with the matching `_response` type — echo the `request_id`, set `approve` true/false:\n\n```json\n{\"to\": \"team-lead\", \"message\": {\"type\": \"shutdown_response\", \"request_id\": \"...\", \"approve\": true}}\n{\"to\": \"researcher\", \"message\": {\"type\": \"plan_approval_response\", \"request_id\": \"...\", \"approve\": false, \"feedback\": \"add error handling\"}}\n```\n\nApproving shutdown terminates your process. Rejecting plan sends the teammate back to revise. Don't originate `shutdown_request` unless asked. Don't send structured JSON status messages — use TaskUpdate.",
  "input_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "to": {
        "type": "string",
        "description": "Recipient: teammate name, or \"*\" for broadcast to all teammates"
      },
      "summary": {
        "type": "string",
        "description": "A 5-10 word summary shown as a preview in the UI (required when message is a string)"
      },
      "message": {
        "oneOf": [
          {
            "type": "string",
            "description": "Plain text message content"
          },
          {
            "type": "object",
            "description": "Structured protocol message",
            "properties": {
              "type": {
                "type": "string",
                "enum": ["shutdown_request", "shutdown_response", "plan_approval_response"]
              },
              "request_id": { "type": "string" },
              "approve": { "type": "boolean" },
              "reason": { "type": "string" },
              "feedback": { "type": "string" }
            },
            "required": ["type"]
          }
        ]
      }
    },
    "required": ["to", "message"],
    "additionalProperties": false
  }
}
```

**设计要点：**
- `shouldDefer: true` — 被 ToolSearch 延迟加载
- `isReadOnly` 动态判断：纯文本消息为 `true`，结构化协议消息为 `false`
- `isEnabled()` 取决于 `isAgentSwarmsEnabled()` feature flag
- message 字段是 union type：纯字符串（日常通信）或结构化对象（协议响应）
- 结构化消息支持三种类型：`shutdown_request`（请求关闭）、`shutdown_response`（响应关闭）、`plan_approval_response`（计划审批响应）
- UDS_INBOX feature flag 启用时额外支持 `uds:<socket-path>` 和 `bridge:<session-id>` 接收方（跨进程/跨会话通信）

---

## 无对应关系的工具

### Claude-Code 独有（MatMaster 无对应）

| Tool | 用途 |
|---|---|
| `NotebookEdit` | Jupyter notebook 单元格编辑 |
| `EnterPlanMode` / `ExitPlanMode` | 规划模式切换 |
| `EnterWorktree` / `ExitWorktree` | Git worktree 隔离 |
| `CronCreate` / `CronDelete` / `CronList` | 定时任务调度 |
| `RemoteTrigger` | 远程 agent 触发器 |
| `TeamCreate` / `TeamDelete` | 多 agent 团队管理 |
| `ListPeers` | 发现可通信的 peer agent |
| `TaskCreate` / `TaskGet` / `TaskUpdate` / `TaskList` / `TaskStop` / `TaskOutput` | Todo v2 任务管理（替代 TodoWrite） |
| `Brief` | 上下文压缩/摘要 |

### MatMaster 独有（Claude-Code 无对应）

| Tool | 用途 |
|---|---|
| `list_dir` | 目录列表（CC 用 Bash + ls 替代） |
| `monitor_job` | Bohrium HPC 作业监控（领域特有） |
