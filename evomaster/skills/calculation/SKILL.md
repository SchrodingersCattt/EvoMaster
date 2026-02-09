---
name: calculation
description: "When using Mat MCP calculation tools (mat_sg_*, mat_dpa_*, mat_doc_*, mat_abacus_*): (1) Use the URL from a previous tool as input when chaining—do not pass only a filename when the previous step returned an https URL. (2) For long-running DPA tasks use submit_* first, then query_job_status, then get_job_results when Done—do not use synchronous tools which may timeout. (3) When polling, wait 30–60 seconds between queries; call get_job_results once status is Done/Success. (4) Input: local paths in workspace are uploaded to OSS automatically; prefer URLs when chaining. (5) Result: download OSS/HTTP result URLs into the workspace so files are available for viewing or post-processing."
license: null
---

Full guide: **job_submit.md** (job submit/poll, **code examples** for URL chaining, submit→poll→get_results, upload, download; then rules). Reserved: **matmaster/** for more materials-calculation content.
