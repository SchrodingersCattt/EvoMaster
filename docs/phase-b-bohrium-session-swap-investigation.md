# Phase B —— Bohrium session swap 专项调查

- **调查日期:** 2026-05-29
- **分支:** `refactor/context`
- **范围(read-only 调查 + 安全死代码清理):**
  `src/services/agent_run_bohrium.py`、`src/services/agent_run_bohrium_stage.py`、
  `matmaster/core/playground.py`(`Playground.attach_session` / `attach_ssh_session` /
  `detach_session` / `cleanup`)
- **关联:** [playground-exp-agent-chain-analysis.md §6 / §11 Phase B](playground-exp-agent-chain-analysis.md)
- **结论一句话:** Bohrium 绕过 `Playground.attach_ssh_session()` 是**有充分理由的**(语义不同),
  本阶段交付「绕过原因的确认」+「Phase 0 漏网死代码清理」;真正的 `swap_execution_session()` API
  实现按 spec 建议**独立成 PR**。

---

## 1. 当前 Bohrium 是怎么换 session 的

生产链路:`AgentRunService.run_agent` → `run_bohrium_stage` → `BohriumSetupService.run_setup`
→ worker 线程里的 `_setup_bohrium_for_run`。换 session 发生在
[`_setup_bohrium_for_run`](../src/services/agent_run_bohrium.py)(节点就绪、拿到 `node_ip`/`node_pwd` 之后):

```text
original_session      = pg.session
original_owns_session = pg._owns_session
ssh_session = SSHSession(SSHSessionConfig(host=node_ip, password=node_pwd,
                                          working_dir=ssh_working_dir,
                                          workspace_path=ssh_working_dir))
ssh_session.open()                                  # ① 先 open
_configure_remote_user_skill_root(ssh_session)
try:
    attach_local_bohrium_runtime_from_run_credentials(ssh_session, run_creds)  # ② 注入 Bohrium runtime
    pg.session      = ssh_session                   # ③ 直改 pg.session
    pg._owns_session = False                        # ④ 关键:不占有
    swapped = True
    _store_bohrium_runtime(session_id,              # ⑤ 把 original 存进全局 SESSIONS,留待 restore
                           original_session=original_session,
                           original_owns_session=original_owns_session,
                           ssh_session=ssh_session)
    _run_clear_remote_proxy(pg, 'post_ssh')
except Exception:
    if swapped:                                     # ⑥ 失败回滚:还原 pg.session/_owns_session + 弹出 SESSIONS
        _restore_playground_session(pg, original_session, original_owns_session)
        SESSIONS.get(session_id, {}).pop('bohrium_runtime', None)
    ssh_session.close()
    raise
runtime = BohriumRuntimeHandle(...); attach_runtime(ssh_session, runtime)  # ⑦ runtime handle attach
```

cleanup 走另一条:`_cleanup_bohrium_after_run` → `_restore_bohrium_runtime_state(session_id, pg)`,
从 `SESSIONS` 弹出 `bohrium_runtime`,`detach_runtime(orig)`、`detach_runtime(ssh) + ssh.close()`,
再 `_restore_playground_session(pg, orig, orig_owns)` 把 `pg.session` 还原成原 session。

> 注意:Bohrium 的 SSH session 切换状态(original/ssh/owns)**不存在 Playground 上**,而是存在
> service 层全局 `SESSIONS[session_id]['bohrium_runtime']` 字典里,由 service 的 setup/cleanup 配对管理。

---

## 2. 为什么不能直接用 `Playground.attach_ssh_session()` / `attach_session()`

逐条核对 spec §11 Phase B 列出的四条假设,**全部成立**:

### ① 所有权语义相反:`_owns_session` 必须是 False
`Playground.attach_session()` 固定 `self._owns_session = True`。而 `Playground.cleanup()` 只在
`_owns_session` 为真时 `self.session.close()`。如果 Bohrium 用 `attach_session`,则:
- `PlaygroundManager.release()` → `pg.cleanup()` 会去 close SSH session;
- 同时 `_cleanup_bohrium_after_run` 也会 close 同一个 ssh_session(它走 `SESSIONS` 弹出 + `ssh.close()`)。

→ **同一 SSH session 被 close 两遍**,且 Playground 误以为自己拥有一个其实由 Bohrium 生命周期管理的资源。
Bohrium 路径明确要 `pg._owns_session = False`,把关闭权交给 Bohrium cleanup。`attach_session` 给不了这个语义。

### ② 必须保留 original session 供 restore,而 attach 会把它关掉
`attach_session()` 在挂新 session 前,会把旧的非 `LocalSession` session **close 掉**
(`self.session.close()` "Previous session closed before attach")。
但 Bohrium 是一次**临时、可恢复**的 swap:run 结束后要把 `pg.session` 还原成 `original_session`
(见 `_restore_bohrium_runtime_state`)。`attach_session` 会提前销毁 original,使"还原"无从谈起。

### ③ 远端工作目录语义不同:不要自动拼 `/{session_id}` 子目录
`attach_ssh_session(..., session_id=X)` 会做 `working_dir = f"{working_dir}/{session_id}"`。
而 Bohrium 用的是 `ssh_working_dir = (remote_workdir or remote_workspace_root).rstrip('/')`,
即 `/share`-style **project-scoped** 远端根目录,**不**追加 session 子目录。直接复用会改变远端路径语义。

### ④ open 后还要注入 Bohrium runtime + 复杂回滚
Bohrium 在 `ssh_session.open()` 之后、swap 之前要 `attach_local_bohrium_runtime_from_run_credentials`,
swap 之后还要 `attach_runtime(ssh_session, BohriumRuntimeHandle(...))`;失败时要**同时**回滚
`pg.session`、`pg._owns_session`、`SESSIONS['bohrium_runtime']`,并 close 半开的 ssh_session。
`attach_session` 只覆盖"挂上并打开",这套 open-then-inject-then-swap-with-rollback 它都不管。

**小结:** Bohrium 需要的是一次「**临时的、非占有的、可恢复的、project-scoped 的** 执行 session 替换」,
这和 `attach_session`(占有 + 关旧 + 自动拼子目录)是**两种不同的操作**。绕过不是疏忽,是语义需要。
`Playground` 那套干净 API 之所以"形同虚设",根因是它没有表达这种 swap 语义的方法。

---

## 3. Phase 0 漏网死代码(本阶段已清理)

Phase 0 删除了 `Playground.agent` 字段,但 `agent_run_bohrium.py` 里两处对 `agent.session` 的镜像写
**没被一起删**,现在恒为死分支(`getattr(pg, 'agent', None)` 永远是 `None`):

- `_restore_playground_session()`:
  ```python
  _agent = getattr(pg, 'agent', None)
  if _agent is not None:
      _agent.session = original_session   # dead: Playground 已无 agent 属性
  ```
- `_setup_bohrium_for_run()`(swap 处):
  ```python
  _agent = getattr(pg, 'agent', None)
  if _agent is not None:
      _agent.session = ssh_session        # dead
  ```

这两处是 spec §10「早期 Playground 直接持有 Agent 的化石」的残留,属 Phase B 范畴的**安全清理**
(纯死分支,无行为变化)。本阶段连同删除。

---

## 4. 建议(留独立 PR 实现):`Playground.swap_execution_session()`

把第 2 节的 swap 语义沉淀为 Playground 上的一个一等方法,让 Bohrium 不再直改 `pg.session`/`pg._owns_session`,
并把切换状态收回 Playground 自身(而非 service 全局 `SESSIONS` 字典)。建议形态(待独立 PR 细化与验证):

```python
def swap_execution_session(self, new_session: Session) -> ExecutionSwapHandle:
    """临时、非占有地把活跃 session 换成 new_session,返回可恢复 handle。

    - 不 close 当前 session(保留为 original 供 restore)
    - new_session 视为外部拥有:swap 期间 self._owns_session = False
    - 不改写远端 working_dir(由调用方按 project-scoped 语义自行决定)
    - handle.restore() 还原 original session 与 _owns_session
    """
```

`ExecutionSwapHandle` 持有 `(original_session, original_owns_session, swapped_session)`,
`restore()` 幂等。Bohrium 改为:`handle = pg.swap_execution_session(ssh_session)`;cleanup 调
`handle.restore()`。这样:

- 删掉 service 层 `SESSIONS['bohrium_runtime']` 那套并行的、靠手工配对的 store/restore;
- `_restore_playground_session` / `_store_bohrium_runtime` / `_restore_bohrium_runtime_state` 收敛;
- 物理事实(当前哪个 session 活跃)只表达一遍(spec §6 指出现在被表达两遍:原地改的 `pg.session`
  + 快照重写的 `with_execution()`)。

**为何不并入 Phase 3:** Phase 3 是 Playground/Exp 之间的契约切分(`ExecutionEnvironment` + `AgentRunRequest`),
而 swap 是 Playground 内部 session 生命周期重构,涉及失败回滚/cleanup 路径的真实行为变更,需要独立的
回滚/并发测试覆盖。两者绑在一个 PR 会让 review 面与回归面同时膨胀。spec §11 Phase B 也明确建议独立成 PR。

---

## 5. 与 Phase 3 的交界(已在 Phase 3 处理)

Phase 3 把原 `pg_ctx.with_bohrium()` / `with_execution()` 迁到 `ExecutionEnvironment` 上,
`run_bohrium_stage` 现在操作 `ExecutionEnvironment`(物理 rebind),返回 `BohriumStageResult.environment`。
这部分是**契约形状**变更,不触及 `_setup_bohrium_for_run` 内部那套 `pg.session` 直改 + `SESSIONS`
store/restore 机制 —— 后者正是本调查建议留给独立 swap PR 的部分。
