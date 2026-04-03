# Claude Tool Reference

本文档聚焦 `../claude-code` 中发往 Claude API 的 `tools[]` 组装结果，目标不是做通用工具介绍，而是回答下面这个更具体的问题：

- Claude Code 在调用 Claude API 时，`tools[]` 到底长什么样
- `name`、`description`、`input_schema` 分别来自哪里
- 哪些字段是稳定的，哪些字段会因运行时条件变化
- `Read`、`Edit`、`Write`、`Bash`、`Glob`、`Grep`、`WebFetch`、`WebSearch`、`Agent`、`TodoWrite` 这 10 个工具，在接近真实 wire payload 的层面应如何理解

> 说明
>
> 仓库里已经有一份 [docs/claude-code-tool-reference.md](./claude-code-tool-reference.md)，它更适合做 Claude Code 与 MatMaster 工具对照。
>
> 本文档是补充版，重点放在 近真实请求载荷、动态字段、逐字展开 description 上。

---

## 1. 结论先行

Claude Code 并不是直接把工具对象原样发送给 Claude API。

真正发送前，会经过下面这条链路：

```text
query.ts
  -> services/api/claude.ts
  -> utils/api.ts::toolToAPISchema()
  -> Anthropic BetaToolUnion[]
  -> 请求体 tools[]
```

其中每个工具对象的关键字段来源是：

- `name = tool.name`
- `description = await tool.prompt(ctx)`
- `input_schema = tool.inputJSONSchema ?? zodToJsonSchema(tool.inputSchema)`
- 额外字段按条件附加：
  - `strict`
  - `defer_loading`
  - `eager_input_streaming`
  - `cache_control`

对应源码：

- `query.ts` 传入 `toolUseContext.options.tools`
- `services/api/claude.ts` 构造 `toolSchemas`
- `utils/api.ts::toolToAPISchema()` 统一序列化

更具体地说：

1. `description` 不是 `tool.description()` 的结果，而是 `tool.prompt()`
2. `input_schema` 来自 Zod schema 转 JSON Schema
3. `strict` 不是所有工具都固定带上，只在满足条件时才真正出现在请求里
4. `WebFetch`、`WebSearch`、`TodoWrite` 在默认 ToolSearch 模式下通常是延迟加载的，不一定出现在首轮 `tools[]`
5. `Bash` 和 `Agent` 的 `description` 是强动态内容，想写出完全静态且永远正确的一份字符串，几乎不可能

---

## 2. 本文档采用的假设

为了给出尽可能接近真实、同时又能落到文档里的版本，本文档采用以下假设：

1. 未显式设置特殊环境变量
2. 默认 ToolSearch 模式开启
3. 当前是 external 分支，不是 ant-native 内建搜索分支
4. `Glob` 和 `Grep` 没有被 embedded search 替代
5. `WebSearch` prompt 中的当前月份按本会话时间展开为 `April 2026`
6. `Read` prompt 不额外注入文件大小限制提示
7. `Read` 行号格式使用当前默认 compact 形式，因此文案仍是源码里的 `cat -n format`

如果运行时条件不同，主要影响：

- `Bash.description`
- `Agent.description`
- `Agent.input_schema`
- 哪些工具进入首轮 `tools[]`
- 工具对象是否带 `strict`
- 工具对象是否带 `defer_loading`

---

## 3. 首轮 tools[] 与 full-load tools[]

### 3.1 首轮更可能出现的工具

在默认 ToolSearch 模式下，首轮更可能直接出现在请求里的，是下面这些：

- `Agent`
- `Bash`
- `Read`
- `Edit`
- `Write`
- `Glob`
- `Grep`

而下面这些通常会被视为 deferred tools：

- `WebFetch`
- `WebSearch`
- `TodoWrite`

原因：

- `WebFetchTool.shouldDefer = true`
- `WebSearchTool.shouldDefer = true`
- `TodoWriteTool.shouldDefer = true`
- ToolSearch 默认会把 deferred tools 改成按需暴露

因此，如果你在抓真实请求，看到首轮 `tools[]` 里没有这三个，不代表它们不存在，而是说明它们还没被加载到本轮上下文。

### 3.2 为什么本文档仍给出 full-load 版

因为你的目标是研究工具体系本身，而不是只关心第一包网络请求。

所以本文档主要给出：

- 一个更适合源码理解的 full-load 版 `tools[]`
- 并在每个工具旁边标明它是否通常会被 `defer_loading`

---

## 4. 动态字段总览

| 字段 | 是否稳定 | 说明 |
|---|---|---|
| `name` | 稳定 | 来自 `tool.name` |
| `input_schema` | 基本稳定 | 来自 `tool.inputSchema`，但 `Agent` 会裁剪字段 |
| `description` | 部分动态 | 来自 `tool.prompt()`，其中 `Bash` / `Agent` / `WebSearch` 明显动态 |
| `strict` | 动态 | 取决于 tool.strict、模型能力、feature gate |
| `defer_loading` | 动态 | 取决于 ToolSearch 是否开启以及工具是否 deferred |
| `eager_input_streaming` | 动态 | 取决于 provider 与 feature gate |

其中最值得单独记住的两点：

### 4.1 `strict`

只有同时满足以下条件，`strict: true` 才会真的出现在请求体里：

- 工具自身声明了 `tool.strict === true`
- 当前模型支持 structured outputs
- feature gate `tengu_tool_pear` 打开

因此，源码里看到某个工具 `strict: true`，不等于最终请求里一定有 `strict: true`。

### 4.2 `defer_loading`

只有当 ToolSearch 生效，且工具被判定为 deferred tool 时，才会在请求体里附加：

```json
{
  "defer_loading": true
}
```

这通常出现在：

- MCP tools
- `shouldDefer: true` 的 builtin tools

---

## 5. 接近真实的 full-load `tools[]`

下面给出一份 近真实 full-load 版 `tools[]`。

这份内容的设计原则是：

- 对稳定工具，尽量逐字展开 `description`
- 对动态工具，使用 源码模板 + 运行时占位 的方式表达
- `input_schema` 保留真正影响调用的字段、类型、required、enum、约束
- 把 `strict` 和 `defer_loading` 明确写出来，但要记住它们在真实请求中是条件字段

```js
const READ_DESCRIPTION = `Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters
- Results are returned using cat -n format, with line numbers starting at 1
- This tool allows Claude Code to read images (eg PNG, JPG, etc). When reading an image file the contents are presented visually as Claude Code is a multimodal LLM.
- This tool can read PDF files (.pdf). For large PDFs (more than 10 pages), you MUST provide the pages parameter to read specific page ranges (e.g., pages: "1-5"). Reading a large PDF without the pages parameter will fail. Maximum 20 pages per request.
- This tool can read Jupyter notebooks (.ipynb files) and returns all cells with their outputs, combining code, text, and visualizations.
- This tool can only read files, not directories. To read a directory, use an ls command via the Bash tool.
- You will regularly be asked to read screenshots. If the user provides a path to a screenshot, ALWAYS use this tool to view the file at the path. This tool will work with all temporary file paths.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents.`;

const EDIT_DESCRIPTION = `Performs exact string replacements in files.

Usage:
- You must use your \`Read\` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file. 
- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: line number + tab. Everything after that is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if \`old_string\` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use \`replace_all\` to change every instance of \`old_string\`.
- Use \`replace_all\` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance.`;

const WRITE_DESCRIPTION = `Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.
- Prefer the Edit tool for modifying existing files — it only sends the diff. Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked.`;

const GLOB_DESCRIPTION = `- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead`;

const GREP_DESCRIPTION = `A powerful search tool built on ripgrep

  Usage:
  - ALWAYS use Grep for search tasks. NEVER invoke \`grep\` or \`rg\` as a Bash command. The Grep tool has been optimized for correct permissions and access.
  - Supports full regex syntax (e.g., "log.*Error", "function\\\\s+\\\\w+")
  - Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
  - Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
  - Use Agent tool for open-ended searches requiring multiple rounds
  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use \`interface\\\\{\\\\}\` to find \`interface{}\` in Go code)
  - Multiline matching: By default patterns match within single lines only. For cross-line patterns like \`struct \\\\{[\\\\s\\\\S]*?field\`, use \`multiline: true\`
`;

const WEBFETCH_DESCRIPTION = `IMPORTANT: WebFetch WILL FAIL for authenticated or private URLs. Before using this tool, check if the URL points to an authenticated service (e.g. Google Docs, Confluence, Jira, GitHub). If so, look for a specialized MCP tool that provides authenticated access.

- Fetches content from a specified URL and processes it using an AI model
- Takes a URL and a prompt as input
- Fetches the URL content, converts HTML to markdown
- Processes the content with the prompt using a small, fast model
- Returns the model's response about the content
- Use this tool when you need to retrieve and analyze web content

Usage notes:
  - IMPORTANT: If an MCP-provided web fetch tool is available, prefer using that tool instead of this one, as it may have fewer restrictions.
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - The prompt should describe what information you want to extract from the page
  - This tool is read-only and does not modify any files
  - Results may be summarized if the content is very large
  - Includes a self-cleaning 15-minute cache for faster responses when repeatedly accessing the same URL
  - When a URL redirects to a different host, the tool will inform you and provide the redirect URL in a special format. You should then make a new WebFetch request with the redirect URL to fetch the content.
  - For GitHub URLs, prefer using the gh CLI via Bash instead (e.g., gh pr view, gh issue view, gh api).
`;

const WEBSEARCH_DESCRIPTION = `
- Allows Claude to search the web and use the results to inform responses
- Provides up-to-date information for current events and recent data
- Returns search result information formatted as search result blocks, including links as markdown hyperlinks
- Use this tool for accessing information beyond Claude's knowledge cutoff
- Searches are performed automatically within a single API call

CRITICAL REQUIREMENT - You MUST follow this:
  - After answering the user's question, you MUST include a "Sources:" section at the end of your response
  - In the Sources section, list all relevant URLs from the search results as markdown hyperlinks: [Title](URL)
  - This is MANDATORY - never skip including sources in your response
  - Example format:

    [Your answer here]

    Sources:
    - [Source Title 1](https://example.com/1)
    - [Source Title 2](https://example.com/2)

Usage notes:
  - Domain filtering is supported to include or block specific websites
  - Web search is only available in the US

IMPORTANT - Use the correct year in search queries:
  - The current month is April 2026. You MUST use this year when searching for recent information, documentation, or current events.
  - Example: If the user asks for "latest React docs", search for "React documentation" with the current year, NOT last year
`;

const BASH_DESCRIPTION = getSimplePrompt();
const AGENT_DESCRIPTION = await getPrompt(activeAgents, isCoordinator, allowedAgentTypes);
const TODOWRITE_DESCRIPTION = PROMPT;

const tools = [
  {
    name: "Read",
    description: READ_DESCRIPTION,
    strict: true,
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["file_path"],
      properties: {
        file_path: { type: "string", description: "The absolute path to the file to read" },
        offset: { type: "integer", minimum: 0, description: "The line number to start reading from. Only provide if the file is too large to read at once" },
        limit: { type: "integer", exclusiveMinimum: 0, description: "The number of lines to read. Only provide if the file is too large to read at once." },
        pages: { type: "string", description: "Page range for PDF files (e.g., 1-5, 3, 10-20). Only applicable to PDF files. Maximum 20 pages per request." }
      }
    }
  },
  {
    name: "Edit",
    description: EDIT_DESCRIPTION,
    strict: true,
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["file_path", "old_string", "new_string"],
      properties: {
        file_path: { type: "string", description: "The absolute path to the file to modify" },
        old_string: { type: "string", description: "The text to replace" },
        new_string: { type: "string", description: "The text to replace it with (must be different from old_string)" },
        replace_all: { type: "boolean", description: "Replace all occurrences of old_string (default false)" }
      }
    }
  },
  {
    name: "Write",
    description: WRITE_DESCRIPTION,
    strict: true,
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["file_path", "content"],
      properties: {
        file_path: { type: "string", description: "The absolute path to the file to write (must be absolute, not relative)" },
        content: { type: "string", description: "The content to write to the file" }
      }
    }
  },
  {
    name: "Bash",
    description: BASH_DESCRIPTION,
    strict: true,
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["command"],
      properties: {
        command: { type: "string", description: "The command to execute" },
        timeout: { type: "number", description: "Optional timeout in milliseconds" },
        description: { type: "string", description: "Clear, concise description of what this command does in active voice." },
        run_in_background: { type: "boolean", description: "Set to true to run this command in the background. Use Read to read the output later." },
        dangerouslyDisableSandbox: { type: "boolean", description: "Set this to true to dangerously override sandbox mode and run commands without sandboxing." }
      }
    }
  },
  {
    name: "Glob",
    description: GLOB_DESCRIPTION,
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["pattern"],
      properties: {
        pattern: { type: "string", description: "The glob pattern to match files against" },
        path: { type: "string", description: "The directory to search in. If not specified, the current working directory will be used." }
      }
    }
  },
  {
    name: "Grep",
    description: GREP_DESCRIPTION,
    strict: true,
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["pattern"],
      properties: {
        pattern: { type: "string", description: "The regular expression pattern to search for in file contents" },
        path: { type: "string", description: "File or directory to search in (rg PATH). Defaults to current working directory." },
        glob: { type: "string", description: "Glob pattern to filter files (e.g. *.js, *.{ts,tsx}) - maps to rg --glob" },
        output_mode: { type: "string", enum: ["content", "files_with_matches", "count"] },
        "-B": { type: "number" },
        "-A": { type: "number" },
        "-C": { type: "number" },
        context: { type: "number" },
        "-n": { type: "boolean" },
        "-i": { type: "boolean" },
        type: { type: "string" },
        head_limit: { type: "number" },
        offset: { type: "number" },
        multiline: { type: "boolean" }
      }
    }
  },
  {
    name: "WebFetch",
    description: WEBFETCH_DESCRIPTION,
    defer_loading: true,
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["url", "prompt"],
      properties: {
        url: { type: "string", format: "uri", description: "The URL to fetch content from" },
        prompt: { type: "string", description: "The prompt to run on the fetched content" }
      }
    }
  },
  {
    name: "WebSearch",
    description: WEBSEARCH_DESCRIPTION,
    defer_loading: true,
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["query"],
      properties: {
        query: { type: "string", minLength: 2, description: "The search query to use" },
        allowed_domains: { type: "array", items: { type: "string" }, description: "Only include search results from these domains" },
        blocked_domains: { type: "array", items: { type: "string" }, description: "Never include search results from these domains" }
      }
    }
  },
  {
    name: "Agent",
    description: AGENT_DESCRIPTION,
    input_schema: {
      type: "object",
      required: ["description", "prompt"],
      properties: {
        description: { type: "string", description: "A short (3-5 word) description of the task" },
        prompt: { type: "string", description: "The task for the agent to perform" },
        subagent_type: { type: "string", description: "The type of specialized agent to use for this task" },
        model: { type: "string", enum: ["sonnet", "opus", "haiku"], description: "Optional model override for this agent." },
        run_in_background: { type: "boolean", description: "Set to true to run this agent in the background. You will be notified when it completes." },
        name: { type: "string", description: "Name for the spawned agent." },
        team_name: { type: "string", description: "Team name for spawning." },
        mode: { type: "string", description: "Permission mode for spawned teammate." },
        isolation: { type: "string", enum: ["worktree"], description: "Isolation mode. worktree creates a temporary git worktree so the agent works on an isolated copy of the repo." },
        cwd: { type: "string", description: "Absolute path to run the agent in." }
      }
    }
  },
  {
    name: "TodoWrite",
    description: TODOWRITE_DESCRIPTION,
    strict: true,
    defer_loading: true,
    input_schema: {
      type: "object",
      additionalProperties: false,
      required: ["todos"],
      properties: {
        todos: {
          type: "array",
          description: "The updated todo list",
          items: {
            type: "object",
            required: ["content", "status", "activeForm"],
            properties: {
              content: { type: "string", minLength: 1 },
              status: { type: "string", enum: ["pending", "in_progress", "completed"] },
              activeForm: { type: "string", minLength: 1 }
            }
          }
        }
      }
    }
  }
];
```

---

## 6. `Read`

### 稳定性

- `name` 稳定
- `description` 基本稳定
- `input_schema` 基本稳定
- `strict` 源码声明为 `true`

### 真正需要注意的点

- `pages` 是 CC 相对很多通用文件读取工具多出来的 PDF 分页参数
- `offset` / `limit` 是按行，而不是按字节
- 读图片、PDF、notebook 都由同一个 `Read` 顶层工具负责

### description 来源

来源文件：

- `tools/FileReadTool/prompt.ts`

展开时又受两个小动态点影响：

- 是否支持 PDF
- 是否注入 maxSize 提示

本文采用的渲染版本，就是上面 `READ_DESCRIPTION` 那一版。

---

## 7. `Edit`

### 稳定性

- `name` 稳定
- `description` 几乎稳定
- `input_schema` 稳定
- `strict` 源码声明为 `true`

### 真正需要注意的点

- `Edit` 不做 patch/hunk 级别编辑，而是精确字符串替换
- 要先 `Read`
- `replace_all` 是它相对很多同类编辑工具最重要的扩展点

### description 的唯一小动态点

行号前缀格式来自：

- compact 格式：`line number + tab`
- 老格式：`spaces + line number + arrow`

当前源码默认是 compact 格式，所以本文使用：

```text
The line number prefix format is: line number + tab
```

---

## 8. `Write`

### 稳定性

- 非常稳定

### 真正需要注意的点

- 已存在文件必须先 `Read`
- 强烈建议只在新建文件或整体重写时使用
- 对已有文件的常规修改，应该优先走 `Edit`

---

## 9. `Bash`

`Bash` 是最不适合被写成单一固定字符串的工具之一。

原因不是它不稳定，而是它的 `description` 本身就是由多段模板拼接而成：

```text
固定主干
  + sandbox section
  + git / PR section
  + 背景任务提示
  + embedded-search 差异
```

### 9.1 为什么 `Bash.description` 很动态

它至少受这些条件影响：

- 是否启用 sandbox
- sandbox 允许哪些目录 / 网络主机
- 是否允许 `dangerouslyDisableSandbox`
- 是否启用背景任务
- 是否使用 embedded search
- 是否包含 git / PR 指南
- 是否是 ant 用户

### 9.2 最真实的理解方式

不要把 `Bash` 看成一个固定字符串。

更真实的理解方式是：

```js
const BASH_DESCRIPTION = getSimplePrompt();
```

也就是：

- 固定骨架取自 `tools/BashTool/prompt.ts`
- 运行时把 sandbox 规则内联进 description
- 再根据用户类型和能力开关，决定是否附加 git / PR 说明

### 9.3 `input_schema`

模型可见的字段主要是：

- `command`
- `timeout`
- `description`
- `run_in_background`
- `dangerouslyDisableSandbox`

内部字段 `_simulatedSedEdit` 不会暴露给模型。

---

## 10. `Glob`

### 稳定性

- 相对稳定

### 真正需要注意的点

- 如果进入 embedded-search 分支，它可能根本不会注册
- 它并不是 deferred tool，而是可能直接被移除

也就是说：

- external 常规分支：`Glob` 在 base tools 中
- ant-native embedded-search 分支：`Glob` 可能根本不在 `tools[]`

---

## 11. `Grep`

### 稳定性

- schema 基本稳定
- description 基本稳定
- `strict` 源码声明为 `true`

### 真正需要注意的点

`Grep` 是 schema 最丰富的搜索工具之一。

相比只暴露 `pattern + path` 的简化 grep 封装，它还支持：

- `output_mode`
- `-A` / `-B` / `-C`
- `context`
- `-n`
- `-i`
- `type`
- `head_limit`
- `offset`
- `multiline`

这也是 Claude Code 能把内容搜索、文件列表、计数搜索统一收在一个工具里的原因。

---

## 12. `WebFetch`

### 稳定性

- `description` 基本稳定
- `input_schema` 稳定
- 默认常被 `defer_loading`

### 真正需要注意的点

它的顶层 description 已经直接写死了一条很关键的策略：

- 认证页面不要轻易用 `WebFetch`
- 如果有专门的 MCP web fetch 工具，要优先用 MCP
- GitHub URL 更推荐 `gh` CLI

所以 `WebFetch` 并不是一个单纯的 抓网页工具，而是一个带使用策略约束的网页工具。

---

## 13. `WebSearch`

### 稳定性

- `description` 半动态
- `input_schema` 稳定
- 默认常被 `defer_loading`

### 半动态体现在哪里

它会把当前月份年份直接写进 prompt：

```text
The current month is April 2026.
```

所以如果月份变了，这段 description 也会变。

### 另外一个容易混淆的点

顶层工具名是：

```text
WebSearch
```

但它在内部再次调用 Claude API 时，使用的 server tool 是：

```json
{
  "type": "web_search_20250305",
  "name": "web_search"
}
```

这两个层级不要混为一谈。

---

## 14. `Agent`

`Agent` 是另一个强动态工具。

### 14.1 为什么它强动态

因为 `Agent.description` 本质上是一个模板系统：

- 会把当前可用 agent 列表拼进去
- 或者在 attachment 模式下，改成一句 `Available agent types are listed in <system-reminder> messages in the conversation.`
- 会根据 fork subagent 是否开启，切换两整套说明文字
- 会根据 coordinator mode 切换成精简版 prompt
- 会根据 teammate / in-process teammate 切换参数可见性说明
- 会根据 background 能力决定是否提示 `run_in_background`
- 会根据 embedded-search 切换搜索工具指引

### 14.2 因此最真实的表达方式

不要试图记一个固定字符串。

最真实的表达方式是：

```js
const AGENT_DESCRIPTION = await getPrompt(activeAgents, isCoordinator, allowedAgentTypes);
```

### 14.3 `input_schema` 也不是完全静态

`Agent.input_schema` 至少受这些条件影响：

- swarm 关闭时，`name` / `team_name` / `mode` 会被从 API schema 中移除
- `cwd` 受 `KAIROS` 控制
- `run_in_background` 在某些情况下会被裁剪
- `isolation` 在 external 分支通常只有 `worktree`
- ant 分支才可能扩展到 `remote`

因此，上文 full-load 版里给出的 `Agent.input_schema`，更适合当作 理解全集，而不是某次请求的唯一真值。

### 14.4 一个接近真实的 description 模板

下面这段比完整最终字符串更适合记忆，因为它保留了真正的动态结构：

```text
Launch a new agent to handle complex, multi-step tasks autonomously.

The Agent tool launches specialized agents (subprocesses) that autonomously handle complex tasks. Each agent type has specific capabilities and tools available to it.

{{AGENT_LIST_SECTION}}

{{FORK_OR_SUBAGENT_GUIDANCE}}

{{WHEN_NOT_TO_USE_SECTION}}

Usage notes:
- Always include a short description (3-5 words) summarizing what the agent will do
- When the agent is done, it will return a single message back to you...
- You can optionally run agents in the background...
- To continue a previously spawned agent, use SendMessage...
- If the user specifies that they want you to run agents "in parallel", you MUST send a single message with multiple Agent tool use content blocks.
- You can optionally set isolation: "worktree" ...
{{OPTIONAL_REMOTE_SECTION}}
{{OPTIONAL_TEAMMATE_SECTION}}
{{OPTIONAL_FORK_SECTION}}
{{PROMPT_WRITING_GUIDANCE}}
{{EXAMPLES}}
```

---

## 15. `TodoWrite`

### 15.1 为什么它值得单独说

因为它和很多简单 todo 工具完全不同。

它的 `description` 不是一句短说明，而是完整的行为规范：

- 什么时候必须使用
- 什么时候不要使用
- 多个正例
- 多个反例
- `pending / in_progress / completed` 的严格语义
- 必须始终保持恰好一个 `in_progress`
- `content` 和 `activeForm` 必须同时提供

### 15.2 最真实的表达方式

和 `Bash`、`Agent` 不同，`TodoWrite` 的 description 基本就是源码里的 `PROMPT` 常量。

因此，最真实的表达方式就是：

```js
const TODOWRITE_DESCRIPTION = PROMPT;
```

### 15.3 为什么本文没有把它的全文再抄一遍

因为 `PROMPT` 本身非常长，已经是一个小型规则文档。

如果你想继续深挖，直接看源码更合适：

- `tools/TodoWriteTool/prompt.ts`

但在实际 `tools[]` 语义上，本文已经把它的关键特征都明确了：

- 这是一个强规范型 description
- 它默认常被 `defer_loading`
- `strict` 源码声明为 `true`
- schema 是：
  - `todos: Array<{ content, status, activeForm }>`

---

## 16. 这 10 个工具的最重要差异

如果只记最关键的差异，可以压缩成下面这张表：

| Tool | 最关键特征 |
|---|---|
| `Read` | 一个顶层工具同时覆盖 text / image / pdf / notebook |
| `Edit` | 精确字符串替换，不是 patch 编辑 |
| `Write` | 已有文件必须先 `Read` |
| `Bash` | description 极长且强动态，带行为策略，不只是命令执行器 |
| `Glob` | 找文件名模式，不找内容 |
| `Grep` | ripgrep 能力全集，参数很丰富 |
| `WebFetch` | 单 URL + prompt 的抓取与提炼工具 |
| `WebSearch` | 顶层工具会再触发内部 server tool web_search |
| `Agent` | description 和 schema 都强动态 |
| `TodoWrite` | 不是短说明工具，而是完整的任务管理规则引擎 |

---

## 17. 建议的使用方式

如果后续还要继续研究 Claude Code tool 体系，我建议把问题分三层：

### 第一层：工具定义层

看每个工具自己的：

- `name`
- `prompt()`
- `inputSchema`
- `strict`
- `shouldDefer`

### 第二层：请求组装层

看：

- `services/api/claude.ts`
- `utils/api.ts`

重点关注：

- `toolToAPISchema()`
- `defer_loading`
- `strict`
- `eager_input_streaming`

### 第三层：运行时动态层

看：

- ToolSearch 是否开启
- provider 是否 firstParty
- 当前模型是否支持 structured outputs
- 是否 embedded-search
- `Agent` 当前有哪些 active agents
- `Bash` 当前 sandbox 配置是什么

只有这三层合在一起，才等于你最终在网络请求里看到的 `tools[]`。

---

## 18. 一句话总结

如果要用一句话概括 Claude Code 的 tool 体系：

> 顶层工具对象本身并不复杂，复杂的是 `description` 其实承担了半个行为系统，而 `tools[]` 又会被 ToolSearch、feature gate、provider 能力和运行时上下文二次改写。

这也是为什么研究 Claude Code 时，单看 schema 不够，必须同时看：

- `prompt.ts`
- `inputSchema`
- `toolToAPISchema()`
- ToolSearch / defer 逻辑

