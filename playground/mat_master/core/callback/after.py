"""After-tool callbacks: MCP error, ask-human, track submit, autodownload, SN, struct_db."""

import json
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .constants import (
    _CHARACTERIZATION_ARTIFACT_KEYS,
    _CHARACTERIZATION_PREFIXES,
    _OSS_URL_RE,
    _SN_PAPER_FIELDS_TO_REMOVE,
    _SN_TOP_LEVEL_FIELDS_TO_REMOVE,
    _extract_artifact_urls,
    is_error_artifact_url,
)


class MatToolCallbacksAfter:
    """Mixin: all after_* callbacks."""

    def after_detect_mcp_business_error(
        self,
        tool_call: Any,
        observation: str,
        info: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Detect business-level errors in MCP tool observation content."""
        if not info.get('mcp_tool'):
            return observation, info
        if 'error' in info:
            return observation, info

        parsed = (
            observation
            if isinstance(observation, dict)
            else self._try_parse_observation_json(observation)
        )
        if parsed is None:
            return observation, info

        error_msg = self._detect_business_error(parsed)
        if error_msg is None:
            return observation, info

        new_info = dict(info)
        new_info['error'] = error_msg
        new_info['success'] = False
        self.logger.warning(
            "MCP business error detected for tool '%s': %s",
            info.get('mcp_tool', '?'),
            error_msg[:200],
        )
        return observation, new_info

    def after_ask_human_interaction(
        self,
        tool_call: Any,
        observation: str,
        info: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Intercept ask-human skill results: emit event and block for user reply."""
        if (tool_call.function.name or '') != 'use_skill':
            return observation, info
        try:
            args = json.loads(tool_call.function.arguments or '{}')
        except (json.JSONDecodeError, TypeError):
            return observation, info
        if not isinstance(args, dict) or args.get('skill_name') != 'ask-human':
            return observation, info

        ah_cfg = getattr(self.agent, '_ask_human_config', {})
        cfg_enabled = ah_cfg.get('enabled', True)
        cfg_timeout = ah_cfg.get('timeout_seconds', 20)

        if not cfg_enabled:
            self.logger.info(
                'ask-human: interactive input disabled (ask_human.enabled=false).'
            )
            return (
                '⚠️ Interactive input is disabled in this environment. '
                'Please decide autonomously: skip this step, retry with modified parameters, or abort.',
                info,
            )

        question = ''
        context = ''
        script_stdout = observation
        if script_stdout.startswith('Script output:\n'):
            script_stdout = script_stdout[len('Script output:\n') :]
        for line in script_stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    question = payload.get('question', question)
                    context = payload.get('context', context)
                    break
            except (json.JSONDecodeError, TypeError):
                if not question:
                    question = line

        if not question:
            question = 'The agent is asking for your input.'

        from playground.mat_master.service.confirm import (
            REPLY_CANCELLED,
            ConfirmMode,
        )

        call_mode_str = ah_cfg.get('mode', 'timeout')
        try:
            call_mode = ConfirmMode(call_mode_str)
        except (ValueError, TypeError):
            call_mode = ConfirmMode.TIMEOUT
        if call_mode == ConfirmMode.BLOCK:
            call_timeout = ah_cfg.get('block_max_wait_seconds', 7200)
        else:
            call_timeout = cfg_timeout

        confirm_mgr = getattr(self.agent, '_confirm_manager', None)
        if confirm_mgr is not None:
            try:
                reply = confirm_mgr.request(
                    question=question,
                    mode=call_mode,
                    timeout_sec=call_timeout,
                    context=context or None,
                    actions=['provide_params', 'skip', 'abort'],
                    origin='ask_human',
                    source_override='MatMaster',
                )
                if reply is REPLY_CANCELLED:
                    self.logger.info('ask-human: user cancelled (stop requested).')
                    return (
                        '⚠️ User cancelled. Please abort or skip this step.',
                        info,
                    )
                if reply is not None:
                    self.logger.info(
                        'ask-human: received reply (%d chars).', len(reply)
                    )
                    return f"User replied: {reply}", info
                self.logger.warning(
                    'ask-human: confirmation timed out (%ds).', call_timeout
                )
                return (
                    f'⚠️ No user response within {call_timeout}s. '
                    'Please decide autonomously: skip this step, retry with modified parameters, or abort.',
                    info,
                )
            except Exception:
                pass

        emit_fn = getattr(self.agent, 'event_callback', None)
        ask_payload: dict = {'question': question}
        if context:
            ask_payload['context'] = context
        if callable(emit_fn):
            emit_fn('MatMaster', 'ask_human', ask_payload)
        else:
            _emit = getattr(self.agent, '_emit', None)
            if callable(_emit):
                _emit('MatMaster', 'ask_human', ask_payload)

        reply_queue: queue.Queue | None = getattr(self.agent, '_ask_human_queue', None)
        if reply_queue is None:
            self.logger.warning(
                'ask-human invoked but no _ask_human_queue is set on the agent. '
                'Returning a placeholder. Set agent._ask_human_queue for interactive mode.'
            )
            return (
                '⚠️ Interactive input is not available in the current execution mode. '
                'The agent asked: ' + question,
                info,
            )

        wait_timeout = call_timeout
        self.logger.info(
            'ask-human: waiting for user reply (mode=%s, timeout=%s)...',
            call_mode.value,
            wait_timeout,
        )
        try:
            reply = reply_queue.get(timeout=wait_timeout)
        except queue.Empty:
            self.logger.warning(
                'ask-human: user reply timed out after %ss.', call_timeout
            )
            return (
                f'⚠️ No user response within {call_timeout}s. '
                'Please decide autonomously: skip this step, retry with modified parameters, or abort.',
                info,
            )

        if reply is None:
            self.logger.info('ask-human: user cancelled (stop requested).')
            return (
                '⚠️ User cancelled. Please abort or skip this step.',
                info,
            )
        self.logger.info('ask-human: received user reply (%d chars).', len(reply))
        return f"User replied: {reply}", info

    def after_track_async_submit(
        self,
        tool_call: Any,
        observation: str,
        info: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Track submit_* jobs in runtime registry for finish-attempt gating."""
        tool_name = tool_call.function.name or ''
        if '_submit_' not in tool_name:
            return observation, info
        if info.get('error') is not None:
            return observation, info

        payload = self._extract_submit_payload(observation)
        if not payload:
            return observation, info

        job_id = payload.get('job_id')
        if not isinstance(job_id, str) or not job_id:
            return observation, info

        extra_info = payload.get('extra_info')
        bohr_job_id = None
        if isinstance(extra_info, dict):
            b = extra_info.get('bohr_job_id')
            if isinstance(b, str) and b:
                bohr_job_id = b

        software = self._derive_software_from_tool_name(tool_name)
        registry = getattr(self.agent, '_job_registry', None)
        if registry is None:
            return observation, info

        registry.record_submit(
            job_id=job_id,
            software=software,
            source_tool=tool_name,
            bohr_job_id=bohr_job_id,
        )
        self.logger.info(
            'after_tool: tracked async submit job_id=%s software=%s bohr_job_id=%s',
            job_id,
            software,
            bohr_job_id,
        )
        return observation, info

    def after_autodownload_oss_results(
        self,
        tool_call: Any,
        observation: str | dict[str, Any],
        info: dict[str, Any],
    ) -> tuple[str | dict[str, Any], dict[str, Any]]:
        """Auto-download OSS artifacts for any mat_* tool."""
        tool_name = tool_call.function.name or ''
        if not tool_name.startswith('mat_'):
            return observation, info
        obs_str = (
            observation if isinstance(observation, str) else json.dumps(observation)
        )
        urls = [u for u in _OSS_URL_RE.findall(obs_str or '') if self._is_oss_url(u)]
        if not urls:
            return observation, info

        self.logger.info(
            '[autodownload] after_autodownload_oss_results urls_count=%s is_remote=%s',
            len(urls),
            self._is_remote,
        )
        download_dir = self._resolve_download_dir()
        if download_dir is None:
            self.logger.info(
                '[autodownload] after_autodownload_oss_results resolve_download_dir=None skip'
            )
            return observation, info
        self._ensure_download_dir(download_dir)

        targets: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen or is_error_artifact_url(url):
                continue
            seen.add(url)
            targets.append(url)

        if not targets:
            return observation, info

        downloaded: list[dict[str, str]] = []

        def _do(u: str) -> tuple[str, str | None]:
            return u, self._download_single(u, download_dir)

        with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
            futures = {pool.submit(_do, u): u for u in targets}
            for fut in as_completed(futures):
                url_key = futures[fut]
                try:
                    _, local_path = fut.result()
                except Exception as e:
                    self.logger.warning(
                        'after_tool: OSS download failed (%s): %s', url_key, e
                    )
                    continue
                if local_path is not None:
                    downloaded.append({'url': url_key, 'local_path': str(local_path)})

        if not downloaded:
            return observation, info

        note_lines = [
            '',
            '[Auto-download callback] Downloaded OSS artifacts to workspace:',
        ]
        for item in downloaded:
            rel = self._to_workspace_rel_path(item['local_path'])
            note_lines.append(f"- {item['url']}")
            note_lines.append(f"  workspace_path: {rel}")
        new_info = dict(info or {})
        new_info['auto_downloaded_files'] = downloaded
        new_info['auto_download_note'] = '\n'.join(note_lines)

        if isinstance(observation, dict):
            return observation, new_info
        base = observation if isinstance(observation, str) else json.dumps(observation)
        new_obs = (base or '') + '\n' + '\n'.join(note_lines)
        return new_obs, new_info

    def after_download_characterization_results(
        self,
        tool_call: Any,
        observation: str,
        info: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Download file artifacts from characterization MCP tool results."""
        tool_name = tool_call.function.name or ''
        if not any(tool_name.startswith(p) for p in _CHARACTERIZATION_PREFIXES):
            return observation, info
        if info.get('error') is not None:
            return observation, info

        parsed = (
            observation
            if isinstance(observation, dict)
            else self._try_parse_observation_json(observation)
        )
        if parsed is None:
            return observation, info

        inner = parsed.get('observation')
        if isinstance(inner, dict):
            parsed = inner

        artifact_urls = _extract_artifact_urls(parsed, _CHARACTERIZATION_ARTIFACT_KEYS)
        if not artifact_urls:
            return observation, info

        already = {d['url'] for d in info.get('auto_downloaded_files', [])}
        new_urls = [u for u in artifact_urls if u not in already]
        if not new_urls:
            return observation, info

        download_dir = self._resolve_download_dir()
        if download_dir is None:
            return observation, info
        self._ensure_download_dir(download_dir)

        downloaded: list[dict[str, str]] = []
        for url in new_urls:
            try:
                dest = self._download_single(url, download_dir)
                if dest is not None:
                    downloaded.append({'url': url, 'local_path': dest})
            except Exception as e:
                self.logger.warning(
                    'Characterization artifact download failed (%s): %s', url, e
                )

        if not downloaded:
            return observation, info

        note_lines = [
            '',
            '[Characterization callback] Downloaded result artifacts to workspace:',
        ]
        for item in downloaded:
            rel = self._to_workspace_rel_path(item['local_path'])
            note_lines.append(f"- {item['url']}")
            note_lines.append(f"  workspace_path: {rel}")

        base = observation if isinstance(observation, str) else json.dumps(observation)
        new_obs = (base or '') + '\n' + '\n'.join(note_lines)
        new_info = dict(info or {})
        existing = list(new_info.get('auto_downloaded_files', []))
        new_info['auto_downloaded_files'] = existing + downloaded
        return new_obs, new_info

    def after_clean_sn_response(
        self,
        tool_call: Any,
        observation: str | dict,
        info: dict[str, Any],
    ) -> tuple[str | dict, dict[str, Any]]:
        """Strip UI/internal/Chinese-translation fields from mat_sn_* tool responses."""
        tool_name: str = getattr(getattr(tool_call, 'function', None), 'name', '') or ''
        if not tool_name.startswith('mat_sn_'):
            return observation, info
        if info.get('error') is not None:
            return observation, info

        obj: dict | None = None
        was_string = False
        if isinstance(observation, dict):
            obj = observation
        elif isinstance(observation, str):
            try:
                obj = json.loads(observation)
                was_string = True
            except (json.JSONDecodeError, ValueError):
                return observation, info

        if not isinstance(obj, dict):
            return observation, info

        for field in _SN_TOP_LEVEL_FIELDS_TO_REMOVE:
            obj.pop(field, None)

        papers = obj.get('data')
        if isinstance(papers, list):
            for paper in papers:
                if isinstance(paper, dict):
                    for field in _SN_PAPER_FIELDS_TO_REMOVE:
                        paper.pop(field, None)

        if was_string:
            observation = json.dumps(obj, ensure_ascii=False)
        else:
            observation = obj

        return observation, info

    def after_survey_reminder(
        self,
        tool_call: Any,
        observation: str | dict,
        info: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Append survey-retrieval reminder after any mat_sn_* search/retrieval tool call."""
        tool_name = tool_call.function.name or ''
        if not tool_name.startswith('mat_sn_'):
            return observation, info
        if info.get('error') is not None:
            return observation, info

        obj = observation if isinstance(observation, dict) else None
        if obj is None and isinstance(observation, str):
            try:
                obj = json.loads(observation)
            except (json.JSONDecodeError, TypeError):
                pass

        n_papers = ''
        zero_results = False
        if isinstance(obj, dict):
            if 'data' in obj and isinstance(obj['data'], list):
                n_papers = str(len(obj['data']))
                zero_results = len(obj['data']) == 0
            elif 'results' in obj and isinstance(obj['results'], list):
                n_papers = str(len(obj['results']))
                zero_results = len(obj['results']) == 0

        call_count = info.get('call_count', '?')
        if zero_results:
            reminder = (
                f"\n\n[Survey reminder: 0 results returned by {tool_name} (retrieval #{call_count}). "
                'Do NOT retry the same tool with the same query. '
                'Switch to a different search tool or method, or try a different query angle.]'
            )
        else:
            reminder = (
                f"\n\n[Survey reminder: {n_papers or '?'} results returned (retrieval #{call_count}). "
                'A thorough survey requires at least 6-15 retrievals; if results are sparse, '
                'vary your query and use a different available search tool or method.]'
            )
        info = {**info, 'survey_reminder': reminder}
        return observation, info

    def after_normalize_struct_db_metadata(
        self,
        tool_call: Any,
        observation: str,
        info: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Normalize mat_struct_db_* retrieval metadata for downstream guard use."""
        tool_name = tool_call.function.name or ''
        if not tool_name.startswith('mat_struct_db_'):
            return observation, info
        if info.get('error') is not None:
            return observation, info

        fallback_level: int = 0
        query_used: str = ''
        candidate_count: int = 0
        obs_obj: Any = None
        try:
            obs_obj = (
                observation
                if isinstance(observation, dict)
                else json.loads(observation)
            )
            if isinstance(obs_obj, dict):
                inner = obs_obj.get('observation', obs_obj)
                if isinstance(inner, str):
                    try:
                        inner = json.loads(inner)
                    except Exception:
                        inner = {}
                if isinstance(inner, dict):
                    fallback_level = int(inner.get('fallback_level', 0) or 0)
                    query_used = str(inner.get('query_used', '') or '')
                    structs = inner.get('structures') or inner.get('results') or []
                    candidate_count = len(structs) if isinstance(structs, list) else 0
        except Exception:
            pass

        new_info = dict(info)
        new_info['retrieval_confidence'] = (
            'direct' if fallback_level == 0 else 'fallback'
        )
        new_info['fallback_level'] = fallback_level
        new_info['query_used'] = query_used
        new_info['candidate_count'] = candidate_count

        if fallback_level > 0:
            annotation = (
                f'\n\n⚠️ [struct-db-metadata] fallback_level={fallback_level}: '
                'This result is an ELEMENT-BASED FALLBACK, not a direct match for '
                'the requested compound. The structures returned may not correspond '
                'to the target material. Do NOT treat these as confirmed results — '
                'use the literature-based search path instead.'
            )
            if isinstance(obs_obj, dict):
                out = dict(obs_obj)
                out['fallback_warning'] = annotation.strip()
                return out, new_info
            obs_str = (
                json.dumps(observation, ensure_ascii=False)
                if isinstance(observation, dict)
                else str(observation)
            )
            return obs_str + annotation, new_info

        return observation, new_info
