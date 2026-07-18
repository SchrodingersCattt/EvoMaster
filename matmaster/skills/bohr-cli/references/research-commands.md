# 科研检索长尾命令

## 科研导师 (mentor)

```bash
bohr mentor "<question>" [--discipline All|Physics|Chemistry|Biology|Materials] [--journal-type foreign|chinese]
```

响应时间 30-60 秒，带文献引用。每次调用约 2 元。

## 文献知识挖掘 (lkm)

```bash
bohr lkm search "<query>" [--top-k 10] [--mode hybrid|semantic|lexical] [--sort comprehensive|relevance|recent]
bohr lkm reasoning --query "<question>"
bohr lkm graph --paper-id <id>
```

## 科学百科 (wiki)

```bash
bohr wiki search "<query>" [--lang zh-CN|en-US]
bohr wiki article <entry_id>
bohr wiki levels
bohr wiki graph <id>
```

## 聚合物文献数据库 (database)

```bash
bohr database tables <db_ak>
bohr database schema <db_ak> <table_ak>
bohr database query <db_ak> <table_ak> [--filter '<json>'] [--limit 20] [--offset 0]
```

只读；`db_ak` 需外部提供（CLI 无枚举入口），内容为聚合物文献数据。
