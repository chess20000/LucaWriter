import json
import re
import time
import hashlib
import threading
import os

from kb_storage import (
    init_db, db_transaction, upsert_chapter, get_chapter, list_chapters_db,
    delete_chapter_artifacts,
    upsert_entity, add_mention, list_entities, match_entities_by_name, remaining_entities,
    get_mentions_by_chapter, get_mentions_for_entity, get_entity_recent_mentions_before,
    add_event, list_events, get_events_by_chapter, list_timeline_events,
    add_foreshadowing, resolve_foreshadowing, list_foreshadowing,
    upsert_rule, add_rule_mention, get_rule_mentions_by_chapter, list_rules,
    upsert_timeline_event_meta, add_timeline_relation, clear_ai_timeline_relations,
    list_timeline_relations, save_consistency_alerts, list_consistency_alerts,
    delete_kb_records,
    set_rt_state, get_rt_state, get_pause_requested, append_stream, rt_log, get_rt_logs,
    embed_upsert, embed_upsert_many, embed_query, embed_clear,
    embed_collection_count, prune_vector_entries,
    hash_content, get_embedding_backend_id, get_embedding_chunk, get_kb_path,
    _get_conn,
)
from embeddings import get_embedding_backend


def _lazy_main():
    from main import (
        call_ai_full, call_ai_stream,
        _get_effective_context_length,
        _read_chapter_file, get_book_meta, get_book_dir, get_work_dir, load_json,
        save_source, _get_source_dir, _write_entity_file,
        save_prediction_md,
        _is_content_empty, _extract_context_summary,
        set_conn_meta,
    )
    return locals()

_MAIN_CACHE = None
def _main():
    global _MAIN_CACHE
    if _MAIN_CACHE is None:
        _MAIN_CACHE = _lazy_main()
    return _MAIN_CACHE


STRUCTURED_SYSTEM_PROMPT = '''你是资料整理员。读完每一章后，提取所有信息，输出严格 JSON。
JSON 必须能被 Python 的 json.loads 解析，不要包含任何多余文字。

实体 type 字段为开放自由词，按本作世界观自拟最贴切的类别。
常见：人物/势力/地点/物品/概念/种族/功法/组织/灵宝/境界/宗门…但不限于此。
不要受限于预设列表，用最能描述该实体本质的词。'''

STRUCTURED_USER_TEMPLATE = '''【章节】{title}
【正文】
{content}

【前情索引（参考，不要复制）】
{prev_context}

【输出 JSON Schema】
{{
  "summary": "本章剧情摘要的自然段落（200-3000 字，连贯叙述，不要 bullet，不要复制原文）",
  "entities": [
    {{
      "canonical_name": "李云",
      "type": "人物",
      "aliases_in_chapter": ["阿云", "李公子"],
      "facts": [
        {{"fact": "本章首次出现，是北境国镇北将军之子", "snippet": "原文引用片段，用于核对"}}
      ]
    }}
  ],
  "events": [
    {{
      "story_time": "开篇之夜",
      "who": "李云",
      "what": "在破庙获得断岳刀",
      "where": "城外破庙",
      "why": "老者临终所托",
      "consequence": "成为后续冲突的核心力量",
      "snippet": "原文引用"
    }}
  ],
  "foreshadowing_new": [
    {{"hint": "老者临终前的低语：黑铁会再开", "snippet": "原文引用"}}
  ],
  "foreshadowing_resolved": [
    {{"earlier_hint": "上一章提到的红衣女子身份", "resolution": "实为青城公主", "snippet": "原文引用"}}
  ],
  "rules": [
    {{"name": "灵气觉醒", "body": "16 岁前可觉醒，过期则废"}}
  ]
}}

【硬性要求】
1. 必须有 summary，其他数组可以为空
2. snippet 必须是原文中实际存在的片段
3. 不要凭空编造任何内容
4. 只输出 JSON，前后不加任何文字、注释、代码块标记'''


# ─── Phase 2: AI Structured Extraction ───

SYSTEM_OVERHEAD_TOKENS = 1500
OUTPUT_PER_CHAPTER_TOKENS = 6000
SAFETY_TOKENS = 2000
# DeepSeek API 的 max_tokens 上限是 393216；大上下文窗口下 remaining 可能远超此值
# 这里用一个保守上限，既能覆盖大批量章节输出，又不会触发 API 400 错误
MAX_OUTPUT_TOKENS = 131072  # 128K，已足够 20+ 章批量输出


def _est_tokens(text: str) -> int:
    return int(len(text or '') * 0.55)


def _compute_output_budget(ctx_len: int, input_chars: int, prev_context: str) -> int:
    if ctx_len <= 0:
        return 8192
    input_tokens = _est_tokens(prev_context) + int(input_chars * 0.55) + SYSTEM_OVERHEAD_TOKENS
    remaining = ctx_len - input_tokens - SAFETY_TOKENS
    return max(4096, min(remaining, MAX_OUTPUT_TOKENS))


def _extract_json_from_text(raw: str) -> str:
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        return m.group(0)
    return raw


def _extract_json_array_from_text(raw: str) -> str:
    if not raw:
        return raw
    text = raw.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
    start = text.find('[')
    end = text.rfind(']')
    if start >= 0 and end > start:
        return text[start:end + 1]
    if start >= 0:
        truncated = text[start:]
        last_close = truncated.rfind('}')
        if last_close > 0:
            candidate = truncated[:last_close + 1].rstrip().rstrip(',') + ']'
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
    return text


def ai_read_chapter_structured(settings, ch, prev_context='', on_token=None, should_stop_fn=None, prior_records=None):
    m = _main()
    call_ai_stream = m['call_ai_stream']

    title = ch['title']
    content = ch['content']

    if m['_is_content_empty'](content):
        return {
            'summary': f'[本章无实质正文，跳过]',
            'entities': [],
            'events': [],
            'foreshadowing_new': [],
            'foreshadowing_resolved': [],
            'rules': [],
        }

    ctx_str = f'【前情索引（只供参考，不要输出）】\n{prev_context}\n\n' if prev_context else ''
    if prior_records and any(prior_records.get(k) for k in ('mentions', 'events', 'rules', 'foreshadowing')):
        ctx_str += (
            '【二周目纠错（重要）】\n'
            '你之前通读过本章并记下了下面这些笔记，但本章正文随后被作者改过，旧笔记可能已过时或本来就有误。\n'
            '请把旧笔记当作"可能有错的记忆"，以当前正文为唯一准绳重新通读并纠错：\n'
            '- 当前正文仍成立的照常输出；与正文冲突、或已被改写/删除的旧内容不要再输出（即纠正/删除）；正文新增的正常补充。\n'
            '最终只输出与当前正文一致的、修正后的完整结构化笔记。\n'
            f'旧笔记：\n{json.dumps(prior_records, ensure_ascii=False)}\n\n'
        )
    prompt = STRUCTURED_USER_TEMPLATE.format(
        title=title,
        content=content,
        prev_context=ctx_str,
    )

    try:
        ctx_len = int(m['_get_effective_context_length'](settings) or 0)
    except Exception:
        ctx_len = 0
    input_chars = len(title or '') + len(content or '') + 240
    output_tokens = _compute_output_budget(ctx_len, input_chars, ctx_str)

    try:
        from main import log_action as _la
        _la('SINGLE_BUDGET', f'ctx={ctx_len} input_chars={input_chars} prev_chars={len(ctx_str)} output_tokens={output_tokens}')
    except Exception:
        pass

    max_retries = 3
    for attempt in range(max_retries):
        if should_stop_fn and should_stop_fn():
            raise StoppedException('用户暂停')

        if attempt > 0:
            retry_prompt = prompt + '\n\n【注意】上次输出格式有误，请只输出严格 JSON，不要包含任何多余文字。'
        else:
            retry_prompt = prompt

        _t0 = time.time()
        raw, err = call_ai_stream(settings, [
            {'role': 'system', 'content': STRUCTURED_SYSTEM_PROMPT},
            {'role': 'user', 'content': retry_prompt},
        ], max_tokens=output_tokens, temperature=0.3, timeout=600,
            on_content_token=(lambda tk: on_token(tk)) if on_token else None,
            should_stop_fn=should_stop_fn)
        try:
            from main import log_action as _la
            _la('SINGLE_STREAM_DONE', f'attempt={attempt+1} elapsed={time.time()-_t0:.1f}s raw_len={len(raw or "")} err={err!r}')
        except Exception:
            pass

        if should_stop_fn and should_stop_fn():
            raise StoppedException('用户暂停')

        if err:
            if err == '用户停止' or (should_stop_fn and should_stop_fn()):
                raise StoppedException('用户暂停')
            if attempt < max_retries - 1:
                continue
            raise RuntimeError(f'AI 调用失败: {err}')

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            extracted = _extract_json_from_text(raw)
            try:
                result = json.loads(extracted)
            except json.JSONDecodeError:
                if attempt < max_retries - 1:
                    continue
                raise RuntimeError(f'JSON 解析失败，原始输出: {raw[:500]}')

        if not isinstance(result, dict) or 'summary' not in result:
            if attempt < max_retries - 1:
                continue
            raise RuntimeError('输出缺少 summary 字段')

        result.setdefault('entities', [])
        result.setdefault('events', [])
        result.setdefault('foreshadowing_new', [])
        result.setdefault('foreshadowing_resolved', [])
        result.setdefault('rules', [])
        return result

    raise RuntimeError('所有重试均失败')


def ai_read_chapters_batch_structured(settings, chapters, prev_context='', on_token=None, should_stop_fn=None):
    m = _main()
    call_ai_stream = m['call_ai_stream']

    parts = []
    for ch in chapters:
        parts.append(f'=== chapter_idx={ch["idx"]} chapter_id={ch["id"]} title={ch["title"]} ===\n{ch["content"]}')

    ctx_str = f'【前情索引（参考，不要复制）】\n{prev_context}\n\n' if prev_context else ''

    prompt = f'''{ctx_str}

以下是当前书连续多章正文。请一次读完，但必须为每一章分别生成独立 JSON 对象，不要把章节内容混在一起。

{chr(10).join(parts)}

输出格式为 JSON 数组，每个元素对应一章，按输入顺序排列：
[
  {{
    "chapter_idx": {chapters[0]["idx"]},
    "summary": "本章剧情摘要，150-800 字，连贯叙述，不要 bullet，不要复制原文",
    "entities": [
      {{
        "canonical_name": "李云",
        "type": "人物",
        "aliases_in_chapter": ["阿云"],
        "facts": [
          {{"fact": "本章发生的事实", "snippet": "原文中实际存在的短片段"}}
        ]
      }}
    ],
    "events": [
      {{
        "story_time": "故事内时间",
        "who": "人物",
        "what": "事件",
        "where": "地点",
        "why": "原因",
        "consequence": "结果",
        "snippet": "原文引用"
      }}
    ],
    "foreshadowing_new": [{{"hint": "新伏笔", "snippet": "原文引用"}}],
    "foreshadowing_resolved": [{{"earlier_hint": "此前伏笔", "resolution": "如何回收", "snippet": "原文引用"}}],
    "rules": [{{"name": "设定名", "body": "设定内容"}}]
  }},
  ...
]

硬性要求：
1. 数组长度必须等于输入章节数，并保持输入顺序
2. 每个对象必须包含 chapter_idx 和 summary
3. snippet 必须是原文中实际存在的片段；没有就留空字符串
4. 不要凭空编造内容
5. 输出要紧凑，但不要漏掉会影响后续问答的人物、事件、设定、数值、伏笔
6. entities 的 type 字段为开放自由词，按本作世界观自拟最贴切的类别（常见：人物/势力/地点/物品/概念/种族/功法/组织…但不限于此）
7. 只输出 JSON 数组，不要任何多余文字、注释、代码块标记。'''

    try:
        ctx_len = int(m['_get_effective_context_length'](settings) or 0)
    except Exception:
        ctx_len = 0
    input_chars = sum(len(c.get('title') or '') + len(c.get('content') or '') + 240 for c in chapters)
    output_tokens = _compute_output_budget(ctx_len, input_chars, ctx_str)
    timeout = min(1800, max(300, len(chapters) * 120))

    try:
        from main import log_action as _la
        _la('BATCH_BUDGET', f'ctx={ctx_len} chapters={len(chapters)} input_chars={input_chars} prev_chars={len(ctx_str)} output_tokens={output_tokens} timeout={timeout}')
    except Exception:
        pass

    max_retries = 2
    for attempt in range(max_retries):
        if should_stop_fn and should_stop_fn():
            raise StoppedException('用户暂停')

        _t0 = time.time()
        raw, err = call_ai_stream(settings, [
            {'role': 'system', 'content': STRUCTURED_SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt},
        ], max_tokens=output_tokens, temperature=0.3, timeout=timeout,
            on_content_token=(lambda tk: on_token(tk)) if on_token else None,
            should_stop_fn=should_stop_fn)
        try:
            from main import log_action as _la
            _la('BATCH_STREAM_DONE', f'attempt={attempt+1} elapsed={time.time()-_t0:.1f}s raw_len={len(raw or "")} err={err!r}')
        except Exception:
            pass

        if err:
            if err == '用户停止' or (should_stop_fn and should_stop_fn()):
                raise StoppedException('用户暂停')
            if attempt < max_retries - 1:
                continue
            raise RuntimeError(f'批量 AI 调用失败: {err}')

        try:
            results = json.loads(raw)
        except json.JSONDecodeError:
            extracted = _extract_json_array_from_text(raw)
            try:
                results = json.loads(extracted)
            except json.JSONDecodeError:
                if attempt < max_retries - 1:
                    continue
                raw_preview = (raw or '').strip()
                head = raw_preview[:200].replace('\n', ' ')
                tail = raw_preview[-200:].replace('\n', ' ') if len(raw_preview) > 200 else ''
                raise RuntimeError(f'批量 JSON 解析失败 (长度={len(raw_preview)} 头={head!r} 尾={tail!r})')

        if isinstance(results, list) and len(results) != len(chapters):
            by_idx = {}
            for item in results:
                if isinstance(item, dict) and item.get('chapter_idx') is not None:
                    try:
                        by_idx[int(item.get('chapter_idx'))] = item
                    except Exception:
                        pass
            if by_idx and all(ch['idx'] in by_idx for ch in chapters):
                results = [by_idx[ch['idx']] for ch in chapters]

        if not isinstance(results, list) or len(results) != len(chapters):
            if attempt < max_retries - 1:
                continue
            got_len = len(results) if isinstance(results, list) else -1
            raise RuntimeError(f'批量输出数组长度不匹配 (期望 {len(chapters)} 实际 {got_len})')

        normalized = []
        valid = True
        for item in results:
            if not isinstance(item, dict) or not item.get('summary'):
                valid = False
                break
            item.setdefault('entities', [])
            item.setdefault('events', [])
            item.setdefault('foreshadowing_new', [])
            item.setdefault('foreshadowing_resolved', [])
            item.setdefault('rules', [])
            normalized.append(item)
        if not valid:
            if attempt < max_retries - 1:
                continue
            raise RuntimeError('批量输出缺少 summary 字段')

        return normalized

    raise RuntimeError('批量模式所有重试均失败')


class StoppedException(Exception):
    pass


# ─── Phase 3: Apply Structured Result ───

def apply_structured_result(book_id, chapter_id, structured, chapter_idx=None):
    delete_chapter_artifacts(book_id, chapter_id)

    for ent_data in structured.get('entities', []) or []:
        canonical = str(ent_data.get('canonical_name', '')).strip()
        if not canonical:
            continue
        aliases = ent_data.get('aliases_in_chapter', []) or []
        ent_id = upsert_entity(
            book_id, canonical, ent_data.get('type', '未分类') or '未分类',
            aliases=aliases, first_chapter_id=chapter_id,
        )
        for fact_data in ent_data.get('facts', []) or []:
            fact = str(fact_data.get('fact', '')).strip()
            if not fact:
                continue
            add_mention(book_id, ent_id, chapter_id,
                        fact=fact,
                        snippet=fact_data.get('snippet'))

    for ev_data in structured.get('events', []) or []:
        what = str(ev_data.get('what', '')).strip()
        if not what:
            continue
        add_event(
            book_id, chapter_id,
            story_time=ev_data.get('story_time', ''),
            who=ev_data.get('who', ''),
            what=what,
            where_loc=ev_data.get('where', ev_data.get('where_loc', '')),
            why=ev_data.get('why', ''),
            consequence=ev_data.get('consequence', ''),
        )

    for fs_data in structured.get('foreshadowing_new', []) or []:
        hint = str(fs_data.get('hint', '')).strip()
        if hint:
            add_foreshadowing(book_id, chapter_id, hint=hint)

    for fs_res_data in structured.get('foreshadowing_resolved', []) or []:
        earlier = str(fs_res_data.get('earlier_hint', '')).strip()
        if earlier:
            resolve_foreshadowing(book_id,
                                  hint=earlier,
                                  resolved_chapter_id=chapter_id,
                                  resolution=fs_res_data.get('resolution', ''))

    for rule_data in structured.get('rules', []) or []:
        name = str(rule_data.get('name', '')).strip()
        body = str(rule_data.get('body', '')).strip()
        if name and body:
            rid = upsert_rule(book_id, name, body=body, first_chapter_id=chapter_id)
            add_rule_mention(book_id, rid, chapter_id, evidence=rule_data.get('snippet') or body[:200])


def affected_sources(structured):
    sources = [('chapter_summary', 'summary')]
    for ent in structured.get('entities', []):
        sources.append(('entity', ent['canonical_name']))
    for ev in structured.get('events', []):
        sources.append(('event', ev.get('what', '')[:80]))
    for fs in structured.get('foreshadowing_new', []):
        sources.append(('foreshadowing', fs.get('hint', '')[:80]))
    for r in structured.get('rules', []):
        sources.append(('rule', r.get('name', '')))
    return sources


def _render_chapter_markdown(book_id, chapter):
    ch = dict(chapter)
    lines = [f'### {ch["title"]}', '', ch.get('summary', '（无摘要）'), '']
    return '\n'.join(lines)


def render_markdown_views(book_id):
    m = _main()
    save_source = m['save_source']
    get_book_dir = m['get_book_dir']
    _get_source_dir = m['_get_source_dir']
    _write_entity_file = m['_write_entity_file']

    chapters = list_chapters_db(book_id)
    entities = list_entities(book_id)

    md_lines = ['# 全书阅读笔记', '']
    for ch in chapters:
        md_lines.append(_render_chapter_markdown(book_id, ch))
    save_source(book_id, '\n'.join(md_lines))

    src_dir = _get_source_dir(book_id)
    ent_dir = os.path.join(src_dir, 'entities')
    os.makedirs(ent_dir, exist_ok=True)
    for ent in entities:
        from kb_storage import get_mentions_for_entity
        mentions = get_mentions_for_entity(book_id, ent['id'])
        lines = [f'# {ent["canonical_name"]}', f'类型：{ent["type"]}', '']
        aliases = json.loads(ent.get('aliases', '[]'))
        if aliases:
            lines.append('别名：' + '、'.join(aliases))
            lines.append('')
        for mref in mentions:
            ci = mref['chapter_idx'] + 1 if mref['chapter_idx'] is not None else '?'
            lines.append(f'### 第 {ci} 章: {mref.get("chapter_title", "")}')
            lines.append('')
            lines.append(f'- {mref["fact"]}')
            if mref.get('snippet'):
                lines.append(f'  > {mref["snippet"]}')
            lines.append('')
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', ent["canonical_name"].strip())[:100] or ent['id']
        _write_entity_file(
            os.path.join(ent_dir, f'{safe_name}.md'),
            '\n'.join(lines)
        )

    events = list_events(book_id)
    if events:
        tl_lines = ['# 时间线', '']
        for ev in events:
            ci = ev.get("chapter_idx")
            ci_str = ci + 1 if ci is not None else '?'
            line = f'- **第 {ci_str} 章** ({ev.get("story_time", "")}): {ev["who"]} — {ev["what"]}'
            if ev.get("where_loc"):
                line += f'（地点：{ev["where_loc"]}）'
            tl_lines.append(line)
        _write_entity_file(
            os.path.join(src_dir, 'timeline.md'),
            '\n'.join(tl_lines)
        )

    fss = list_foreshadowing(book_id)
    if fss:
        fs_lines = ['# 伏笔与线索', '']
        open_fs = [f for f in fss if f['status'] == 'open']
        resolved_fs = [f for f in fss if f['status'] == 'resolved']
        if open_fs:
            fs_lines.append('## 未解伏笔')
            for f in open_fs:
                ci = f.get("chapter_idx")
                ci_str = ci + 1 if ci is not None else '?'
                fs_lines.append(f'- {f["hint"]}（第 {ci_str} 章）')
        if resolved_fs:
            fs_lines.append('')
            fs_lines.append('## 已解伏笔')
            for f in resolved_fs:
                ci = f.get("chapter_idx")
                ci_str = ci + 1 if ci is not None else '?'
                fs_lines.append(f'- {f["hint"]} → {f["resolution"]}（第 {ci_str} 章）')
        _write_entity_file(
            os.path.join(src_dir, 'foreshadowing.md'),
            '\n'.join(fs_lines)
        )


# ─── Incremental Embed ───

def incremental_embed(book_id, settings, sources=None):
    try:
        m = _main()
        m['set_conn_meta']('embedding', '建立向量索引', book_id)
    except Exception:
        pass
    try:
        backend = get_embedding_backend(settings)
    except ImportError as e:
        rt_log(book_id, f'嵌入后端不可用: {e}')
        return
    except Exception as e:
        rt_log(book_id, f'嵌入后端初始化失败: {e}')
        return
    stored_backend = get_embedding_backend_id(book_id)
    if stored_backend and stored_backend != backend.backend_id:
        embed_clear(book_id)
        rt_log(book_id, '嵌入后端已变更，已重建向量集合')
    texts_to_embed = []
    ids_to_embed = []
    metadatas_to_embed = []
    hashes_to_register = []
    expected_ids = set()

    def queue_chunk(chunk_id, text, source_type, source_id, extra_meta=None):
        expected_ids.add(chunk_id)
        content_hash = hash_content(text)
        old = get_embedding_chunk(book_id, chunk_id)
        if old and old.get('content_hash') == content_hash and old.get('backend_id') == backend.backend_id:
            return
        texts_to_embed.append(text)
        ids_to_embed.append(chunk_id)
        meta = {'source_type': source_type, 'source_id': source_id}
        if extra_meta:
            meta.update(extra_meta)
        metadatas_to_embed.append(meta)
        hashes_to_register.append((chunk_id, source_type, source_id, content_hash))

    chapters = list_chapters_db(book_id)
    for ch in chapters:
        if ch['status'] != 'done' or not ch.get('summary'):
            continue
        cid = f'ch_summary_{ch["id"]}'
        text = f'章节: {ch["title"]}\n{ch["summary"]}'
        queue_chunk(cid, text, 'chapter_summary', ch['id'])

    entities = list_entities(book_id)
    for ent in entities:
        from kb_storage import get_mentions_for_entity
        mentions = get_mentions_for_entity(book_id, ent['id'])
        if not mentions:
            continue
        cid = f'ent_{ent["id"]}'
        text = f'实体: {ent["canonical_name"]} ({ent["type"]})\n'
        for m in mentions:
            text += f'- [{m.get("chapter_title","?")}] {m["fact"]}\n'
        queue_chunk(cid, text, 'entity', ent['id'], extra_meta={'kind': ent['type']})

    events = list_events(book_id)
    for ev in events:
        ev_text = f'事件: 第{ev.get("chapter_idx","?")}章 {ev["who"]} {ev["what"]}'
        if ev.get('where_loc'):
            ev_text += f' 地点: {ev["where_loc"]}'
        if ev.get('why'):
            ev_text += f' 原因: {ev["why"]}'
        if ev.get('consequence'):
            ev_text += f' 结果: {ev["consequence"]}'
        if ev.get('story_time'):
            ev_text += f' 时间: {ev["story_time"]}'
        queue_chunk(f'ev_{ev["id"]}', ev_text, 'event', ev['id'])

    fss = list_foreshadowing(book_id)
    for f in fss:
        status = '未解' if f['status'] == 'open' else '已解'
        fs_text = f'伏笔 [{status}]: {f["hint"]}'
        if f.get('resolution'):
            fs_text += f' → 回收: {f["resolution"]}'
        queue_chunk(f'fs_{f["id"]}', fs_text, 'foreshadowing', f['id'])

    rules = list_rules(book_id)
    for r in rules:
        r_text = f'规则: {r["name"]}: {r["body"]}'
        queue_chunk(f'rule_{r["id"]}', r_text, 'rule', r['id'])

    if texts_to_embed:
        vecs = backend.embed(texts_to_embed)
        embed_upsert_many(
            book_id,
            ids_to_embed,
            texts_to_embed,
            vecs,
            metadatas_to_embed,
        )
        from kb_storage import register_embedding_chunk
        for chunk_id, source_type, source_id, content_hash in hashes_to_register:
            register_embedding_chunk(book_id, chunk_id, source_type, source_id,
                                     content_hash, backend.backend_id)
    prune_vector_entries(book_id, expected_ids)


# ─── Phase 3: Readthrough Orchestrator ───

# COO v2 卷次切换提示（契约 coo-format.md §5.4，文案两端必须一致）
VOLUME_BOUNDARY_PROMPT = '新一卷《{book_title}》开始了。故事延续自世界观「{work_title}」。'


def _build_prev_context(book_id, max_chars=6000):
    chapters = [c for c in list_chapters_db(book_id) if c.get('status') == 'done' and c.get('summary')]
    if not chapters:
        return ''
    lines = ['# 已入库前情索引', '']
    recent = chapters[-8:]
    for ch in recent:
        idx = ch.get('idx')
        idx_text = f'第 {idx + 1} 章' if idx is not None else '章节'
        block = f'## {idx_text}: {ch.get("title", "")}\n{ch.get("summary", "")}'
        if sum(len(x) for x in lines) + len(block) > max_chars:
            break
        lines.append(block)
        lines.append('')

    entities = list_entities(book_id)[:40]
    if entities and sum(len(x) for x in lines) < max_chars:
        lines.append('## 已识别实体')
        for ent in entities:
            item = f'- {ent["canonical_name"]}（{ent["type"]}）'
            if sum(len(x) for x in lines) + len(item) > max_chars:
                break
            lines.append(item)
    return '\n'.join(lines)[:max_chars]


def do_readthrough(book_id, settings, config=None, resume=False):
    m = _main()
    set_conn_meta = m['set_conn_meta']
    _read_chapter_file = m['_read_chapter_file']
    get_book_meta = m['get_book_meta']
    _extract_context_summary = m['_extract_context_summary']
    _get_effective_context_length = m['_get_effective_context_length']
    _is_content_empty = m['_is_content_empty']
    render_markdown_views_fn = render_markdown_views

    set_conn_meta('readthrough', '摘要', book_id)
    cfg = config or {}
    now = int(time.time())

    try:
        init_db(book_id)

        meta = get_book_meta(book_id) or {}
        order = meta.get('chapter_order', [])
        ch_dir = os.path.join(m['get_book_dir'](book_id), 'chapters')
        if not os.path.isdir(ch_dir) or not order:
            set_rt_state(book_id, status='error', phase='没有章节', error='没有章节')
            return

        total = len(order)
        set_rt_state(book_id, status='running', phase='准备中', total=total,
                     current_idx=-1, active_start_idx=-1, active_end_idx=-1,
                     pause_requested=0, stream_buffer='', error='')
        rt_log(book_id, f'全书 {total} 章')

        chapters = []
        for i, cid in enumerate(order):
            ch = _read_chapter_file(book_id, cid)
            if ch:
                chapters.append({'idx': i, 'id': cid, 'title': ch.get('title', f'第{i+1}章'), 'content': ch.get('content', '')})

        context_window = _get_effective_context_length(settings)
        try:
            context_window = int(context_window or 0)
        except Exception:
            context_window = 0
        read_context_window = context_window if context_window > 0 else 65536

        def _max_batch_chapters(ctx_len):
            try:
                ctx_len = int(ctx_len or 0)
            except Exception:
                ctx_len = 0
            if ctx_len < 16000:
                return 1
            # Per chapter ~1500 tokens input + 6000 output = 7500
            # Reserve 4000 for prev_ctx + system overhead
            usable = int(ctx_len * 0.90) - 4000
            n = usable // 7500
            return max(2, n)

        def _prev_context_limit(ctx_len):
            try:
                ctx_len = int(ctx_len or 0)
            except Exception:
                ctx_len = 0
            if ctx_len <= 0:
                return 4000
            return max(2500, min(12000, ctx_len // 20))

        def _chapter_input_cost(ch):
            return len(ch.get('title') or '') + len(ch.get('content') or '') + 240

        def _batch_fits(ctx_len, prev_ctx, used_chars, chapter_count):
            try:
                ctx_len = int(ctx_len or 0)
            except Exception:
                ctx_len = 0
            if ctx_len <= 0:
                return False
            input_tokens = _est_tokens(prev_ctx) + int(used_chars * 0.55) + SYSTEM_OVERHEAD_TOKENS
            output_reserve = chapter_count * OUTPUT_PER_CHAPTER_TOKENS
            return (input_tokens + output_reserve + SAFETY_TOKENS) <= int(ctx_len * 0.90)

        max_batch_chapters = _max_batch_chapters(read_context_window)
        use_batch = bool(read_context_window > 0 and max_batch_chapters > 1)
        # 尊重用户选择的阅读模式
        if cfg.get('read_mode') == 'chapter':
            use_batch = False
        if use_batch:
            if context_window > 0:
                rt_log(book_id, f'上下文限制: {context_window} tokens，最多 {max_batch_chapters} 章/批')
            else:
                rt_log(book_id, f'未设置上下文长度，按 65536 tokens 保守快读，最多 {max_batch_chapters} 章/批')
        else:
            if context_window > 0:
                rt_log(book_id, f'上下文限制仅 {context_window} tokens，使用单章模式')
            else:
                rt_log(book_id, '未设置上下文长度，使用单章模式')
        if cfg.get('read_mode') == 'chapter':
            rt_log(book_id, '用户选择了逐章精读，已强制使用单章模式')

        def stop_check():
            st = get_rt_state(book_id)
            return bool(st and st['pause_requested'])

        current_max_batch = max_batch_chapters

        def _unchanged_done(ch):
            existing = get_chapter(book_id, ch['id'])
            return bool(existing and existing['status'] == 'done' and existing['content_hash'] == hash_content(ch['content']))

        def _is_reread(ch):
            # 之前已通读完成、本次因正文改动需要重读的章 → 走二周目纠错（带旧记录），不参与批量
            existing = get_chapter(book_id, ch['id'])
            return bool(existing and existing['status'] == 'done')

        def _mark_empty_done(ch):
            rt_log(book_id, f'跳过空章节: {ch["title"]}')
            delete_chapter_artifacts(book_id, ch['id'])
            upsert_chapter(book_id, ch['id'], idx=ch['idx'], title=ch['title'],
                          status='done',
                          summary='[本章无实质正文，跳过]',
                          content_hash=hash_content(ch['content']),
                          error='')

        def _finish_structured(ch, structured):
            apply_structured_result(book_id, ch['id'], structured, chapter_idx=ch['idx'])
            upsert_chapter(book_id, ch['id'], idx=ch['idx'], title=ch['title'],
                          status='done',
                          summary=structured['summary'],
                          content_hash=hash_content(ch['content']),
                          error='')

        def _process_single(ch):
            if stop_check():
                set_rt_state(book_id, status='paused', phase='已暂停',
                             current_idx=ch['idx'], active_start_idx=-1, active_end_idx=-1,
                             stream_buffer='通读已暂停，进度已保存。')
                rt_log(book_id, '用户暂停')
                return False

            reread_prior = _chapter_kb_records_for_reread(book_id, ch['id']) if _is_reread(ch) else None
            set_rt_state(book_id, current_idx=ch['idx'],
                         phase=(f'重读纠错: {ch["title"]}' if reread_prior else f'读: {ch["title"]}'),
                         active_start_idx=ch['idx'], active_end_idx=ch['idx'],
                         stream_buffer=(f'第 {ch["idx"] + 1} 章有改动，带着旧笔记重读纠错…' if reread_prior else f'正在思考第 {ch["idx"] + 1} 章：{ch["title"]}'))
            upsert_chapter(book_id, ch['id'], idx=ch['idx'], title=ch['title'],
                          content_hash=hash_content(ch['content']), status='processing', error='')

            try:
                prev_ctx = _build_prev_context(book_id, max_chars=_prev_context_limit(read_context_window))
                structured = ai_read_chapter_structured(
                    settings, ch, prev_context=prev_ctx, prior_records=reread_prior,
                    on_token=lambda _tk: set_rt_state(book_id, stream_buffer='模型已返回，正在写入知识库...'),
                    should_stop_fn=stop_check,
                )
            except StoppedException:
                upsert_chapter(book_id, ch['id'], status='pending')
                set_rt_state(book_id, status='paused', phase='已暂停',
                             current_idx=ch['idx'], active_start_idx=-1, active_end_idx=-1,
                             stream_buffer='通读已暂停，继续后会重读当前章。')
                rt_log(book_id, '用户暂停（章节中），下次重做此章')
                return False
            except Exception as e:
                if stop_check():
                    upsert_chapter(book_id, ch['id'], status='pending')
                    set_rt_state(book_id, status='paused', phase='已暂停',
                                 current_idx=ch['idx'], active_start_idx=-1, active_end_idx=-1,
                                 stream_buffer='通读已暂停，继续后会重读当前章。')
                    rt_log(book_id, '用户暂停（请求已中断）')
                    return False
                upsert_chapter(book_id, ch['id'], status='failed', error=str(e)[:500])
                rt_log(book_id, f'失败: {ch["title"]} ({str(e)[:100]})')
                return True

            set_rt_state(book_id, current_idx=ch['idx'], phase=f'入库: {ch["title"]}',
                         active_start_idx=ch['idx'], active_end_idx=ch['idx'],
                         stream_buffer='正在整理人物、事件、伏笔和规则...')
            _finish_structured(ch, structured)
            done_count = len([c for c in list_chapters_db(book_id) if c['status'] == 'done'])
            rt_log(book_id, f'完成 ({done_count}/{total}) {ch["title"]}')
            return True

        pos = 0
        while pos < len(chapters):
            ch = chapters[pos]
            if stop_check():
                set_rt_state(book_id, status='paused', phase='已暂停',
                             current_idx=ch['idx'], active_start_idx=-1, active_end_idx=-1,
                             stream_buffer='通读已暂停，进度已保存。')
                rt_log(book_id, '用户暂停')
                return

            if _unchanged_done(ch):
                upsert_chapter(book_id, ch['id'], idx=ch['idx'], title=ch['title'])
                set_rt_state(book_id, current_idx=ch['idx'], phase=f'跳过: {ch["title"]}',
                             active_start_idx=-1, active_end_idx=-1,
                             stream_buffer=f'第 {ch["idx"] + 1} 章已有最新笔记，已跳过。')
                pos += 1
                continue

            if _is_content_empty(ch['content']):
                set_rt_state(book_id, current_idx=ch['idx'], phase=f'跳过: {ch["title"]}',
                             active_start_idx=-1, active_end_idx=-1,
                             stream_buffer=f'第 {ch["idx"] + 1} 章没有正文内容，已跳过。')
                _mark_empty_done(ch)
                pos += 1
                continue

            prev_ctx = _build_prev_context(book_id, max_chars=_prev_context_limit(read_context_window))
            batch = [ch]
            if use_batch and current_max_batch > 1 and not _is_reread(ch):
                used = _chapter_input_cost(ch)
                scan = pos + 1
                while scan < len(chapters) and len(batch) < current_max_batch:
                    nxt = chapters[scan]
                    if stop_check() or _unchanged_done(nxt) or _is_content_empty(nxt['content']) or _is_reread(nxt):
                        break
                    nxt_cost = _chapter_input_cost(nxt)
                    if not _batch_fits(read_context_window, prev_ctx, used + nxt_cost, len(batch) + 1):
                        break
                    batch.append(nxt)
                    used += nxt_cost
                    scan += 1

            if use_batch and len(batch) > 1:
                first_no = batch[0]['idx'] + 1
                last_no = batch[-1]['idx'] + 1
                set_rt_state(book_id, current_idx=batch[0]['idx'],
                             phase=f'批量读: 第 {first_no}-{last_no} 章',
                             active_start_idx=batch[0]['idx'], active_end_idx=batch[-1]['idx'],
                             stream_buffer=f'正在思考第 {first_no}-{last_no} 章，一次处理 {len(batch)} 章。')
                rt_log(book_id, f'批量读取 第 {first_no}-{last_no} 章（{len(batch)} 章）')
                for item in batch:
                    upsert_chapter(book_id, item['id'], idx=item['idx'], title=item['title'],
                                  content_hash=hash_content(item['content']),
                                  status='processing', error='')
                try:
                    results = ai_read_chapters_batch_structured(
                        settings, batch, prev_context=prev_ctx,
                        on_token=lambda _tk: set_rt_state(book_id, stream_buffer='模型已返回，正在拆分章节结果...'),
                        should_stop_fn=stop_check,
                    )
                    if stop_check():
                        raise StoppedException('用户暂停')
                    for item, structured in zip(batch, results):
                        set_rt_state(book_id, current_idx=item['idx'],
                                     phase=f'入库: {item["title"]}',
                                     active_start_idx=batch[0]['idx'], active_end_idx=batch[-1]['idx'],
                                     stream_buffer=f'正在写入第 {item["idx"] + 1} 章笔记。')
                        _finish_structured(item, structured)
                    done_count = len([c for c in list_chapters_db(book_id) if c['status'] == 'done'])
                    rt_log(book_id, f'批次完成 ({done_count}/{total}) 第 {first_no}-{last_no} 章')
                    pos += len(batch)
                    continue
                except StoppedException:
                    for item in batch:
                        upsert_chapter(book_id, item['id'], status='pending')
                    set_rt_state(book_id, status='paused', phase='已暂停',
                                 current_idx=batch[0]['idx'], active_start_idx=-1, active_end_idx=-1,
                                 stream_buffer='通读已暂停，继续后会重读当前批次。')
                    rt_log(book_id, '用户暂停（批次中），下次重做当前批次')
                    return
                except Exception as e:
                    for item in batch:
                        upsert_chapter(book_id, item['id'], status='pending', error=str(e)[:500])
                    new_max_batch = max(1, current_max_batch // 2)
                    if new_max_batch < current_max_batch:
                        current_max_batch = new_max_batch
                        if current_max_batch <= 1:
                            rt_log(book_id, f'批量失败（{str(e)[:120]}），缩到单章模式')
                        else:
                            rt_log(book_id, f'批量失败（{str(e)[:120]}），下批缩到 {current_max_batch} 章')
                    else:
                        rt_log(book_id, f'批量失败：{str(e)[:120]}')
                    set_rt_state(book_id, current_idx=batch[0]['idx'],
                                 phase=f'逐章重试: 第 {first_no}-{last_no} 章',
                                 active_start_idx=batch[0]['idx'], active_end_idx=batch[-1]['idx'],
                                 stream_buffer=f'批量失败，先用单章模式补完 第 {first_no}-{last_no} 章，下批再尝试 {current_max_batch} 章/批。')
                    for batch_ch in batch:
                        if not _process_single(batch_ch):
                            return
                    pos += len(batch)
                    continue

            if not _process_single(ch):
                return
            pos += 1

        set_rt_state(book_id, phase='建立索引', active_start_idx=-1, active_end_idx=-1,
                     stream_buffer='正文已经通读完，正在统一建立检索索引。')
        rt_log(book_id, '统一建立检索索引')
        incremental_embed(book_id, settings)
        render_markdown_views_fn(book_id)
        failed = [c for c in list_chapters_db(book_id) if c.get('status') == 'failed']
        if failed:
            msg = f'{len(failed)} 章失败，可点击继续重试'
            set_rt_state(book_id, status='error', phase=msg, current_idx=-1,
                         active_start_idx=-1, active_end_idx=-1, error=msg)
            rt_log(book_id, msg)
            return

        set_rt_state(book_id, status='done', phase='完成', current_idx=-1,
                     active_start_idx=-1, active_end_idx=-1)
        rt_log(book_id, '通读完成')

        meta = get_book_meta(book_id) or {}
        meta['readthrough_at'] = time.time()
        from main import save_json
        save_json(os.path.join(m['get_book_dir'](book_id), 'meta.json'), meta)

    except Exception as e:
        import traceback
        err = f'通读崩溃: {str(e)[:200]}'
        rt_log(book_id, err)
        set_rt_state(book_id, status='error', phase='崩溃', error=err + '\n' + traceback.format_exc()[-500:])


# ─── COO v2: 世界观级通读（按 reading_order 遍历） ───

def _resolve_reading_order(work_id, books_meta, reading_order=None):
    """解析阅读线。若未提供则按缺省规则推导（coo-format.md §5.3）。

    返回有序列表，每项为:
      {type, book_id?, chapter_id?, chapter_title?, book_title?, work_title?, lore_ref?, prompt_override?}
    """
    m = _main()
    ordered_books = sorted(books_meta, key=lambda b: b.get('order', 0))
    book_map = {b.get('id'): b for b in ordered_books if b.get('id')}
    raw_order = list(reading_order or [])
    if not raw_order:
        for b in ordered_books:
            meta = m['get_book_meta'](b['id']) or {}
            for cid in meta.get('chapter_order', []):
                raw_order.append({'type': 'chapter', 'book': b['id'], 'chapter': cid})

    result = []
    previous_chapter_book = None
    boundary_for_next_book = None
    chapter_position = 0
    for raw in raw_order:
        if not isinstance(raw, dict):
            continue
        item_type = raw.get('type', 'chapter')
        if item_type == 'volume_boundary':
            bid = raw.get('book') or raw.get('book_id')
            if bid not in book_map:
                continue
            item = {
                'type': 'volume_boundary',
                'book_id': bid,
                'book_title': book_map[bid].get('title', ''),
                'prompt_override': raw.get('prompt_override', ''),
            }
            result.append(item)
            boundary_for_next_book = bid
            continue
        if item_type == 'lore':
            ref = raw.get('ref')
            if not ref:
                continue
            lore_path = os.path.join(m['get_work_dir'](work_id), 'lore', f'{ref}.json')
            lore = m['load_json'](lore_path, dict) if os.path.isfile(lore_path) else {}
            if not lore:
                continue
            result.append({
                'type': 'lore',
                'ref': ref,
                'title': lore.get('title', '未命名设定'),
                'kind': lore.get('kind', ''),
                'content': lore.get('content', ''),
                'note': raw.get('note', ''),
            })
            continue
        if item_type != 'chapter':
            continue
        bid = raw.get('book') or raw.get('book_id')
        cid = raw.get('chapter') or raw.get('chapter_id')
        if bid not in book_map or not cid:
            continue
        ch = m['_read_chapter_file'](bid, cid)
        if not ch:
            continue
        if previous_chapter_book and previous_chapter_book != bid and boundary_for_next_book != bid:
            result.append({
                'type': 'volume_boundary',
                'book_id': bid,
                'book_title': book_map[bid].get('title', ''),
                'prompt_override': '',
            })
        result.append({
            'type': 'chapter',
            'book_id': bid,
            'book_title': book_map[bid].get('title', ''),
            'chapter_id': cid,
            'chapter_title': ch.get('title', f'第{chapter_position + 1}章'),
            'chapter_idx': chapter_position,
        })
        chapter_position += 1
        previous_chapter_book = bid
        boundary_for_next_book = None
    return result


def do_readthrough_work(work_id, books_meta, settings, reading_order=None, work_title='', config=None, resume=False):
    """COO v2 世界观级通读：按 reading_order 遍历所有子书章节 + lore + 卷次边界。

    work_id: 世界观 ID（用作共享知识库的 book_id）
    books_meta: [{id, title, order, ...}] 子书列表
    reading_order: 阅读线数组（可选，缺省自动推导）
    work_title: 世界观标题（用于卷次提示）
    """
    m = _main()
    set_conn_meta = m['set_conn_meta']
    _read_chapter_file = m['_read_chapter_file']
    _get_effective_context_length = m['_get_effective_context_length']
    _is_content_empty = m['_is_content_empty']
    render_markdown_views_fn = render_markdown_views

    set_conn_meta('readthrough', '世界观通读', work_id)
    cfg = config or {}
    now = int(time.time())

    try:
        init_db(work_id)

        # 解析阅读线
        order = _resolve_reading_order(work_id, books_meta, reading_order)
        # 回填 work_title 到 volume_boundary 条目
        for item in order:
            if item.get('type') == 'volume_boundary':
                item['work_title'] = work_title

        if not order:
            set_rt_state(work_id, status='error', phase='没有章节', error='阅读线为空')
            return

        total = len([x for x in order if x['type'] in ('chapter', 'lore')])
        set_rt_state(work_id, status='running', phase='准备中', total=total,
                     current_idx=-1, active_start_idx=-1, active_end_idx=-1,
                     pause_requested=0, stream_buffer='', error='')
        rt_log(work_id, f'世界观通读: {len(order)} 个阅读单元 ({total} 章)')

        context_window = _get_effective_context_length(settings)
        try:
            context_window = int(context_window or 0)
        except Exception:
            context_window = 0
        read_context_window = context_window if context_window > 0 else 65536

        def stop_check():
            st = get_rt_state(work_id)
            return bool(st and st['pause_requested'])

        # 注入卷次边界系统消息
        def _inject_boundary(item):
            """在 AI 上下文中注入卷次切换提示。"""
            prompt = item.get('prompt_override') or VOLUME_BOUNDARY_PROMPT
            msg = prompt.format(
                book_title=item.get('book_title', ''),
                work_title=item.get('work_title', work_title),
            )
            rt_log(work_id, f'📖 {msg}')
            # 将提示作为上下文的一部分注入（通过追加到 stream_buffer 可见）
            set_rt_state(work_id, stream_buffer=msg)

        # 逐单元处理
        chapter_count = 0
        for pos, item in enumerate(order):
            if stop_check():
                set_rt_state(work_id, status='paused', phase='已暂停',
                             current_idx=pos, active_start_idx=-1, active_end_idx=-1,
                             stream_buffer='通读已暂停，进度已保存。')
                rt_log(work_id, '用户暂停')
                return

            item_type = item.get('type', 'chapter')

            if item_type == 'volume_boundary':
                _inject_boundary(item)
                # volume_boundary 不清空记忆，仅注入提示后继续
                continue

            elif item_type == 'lore':
                lore_ref = item.get('ref', '')
                lore_title = item.get('title') or lore_ref or '未命名设定'
                content = item.get('content', '')
                storage_id = f'lore::{lore_ref}'
                if _is_content_empty(content):
                    rt_log(work_id, f'跳过空设定: {lore_title}')
                    continue
                existing = get_chapter(work_id, storage_id)
                if existing and existing['status'] == 'done' and existing.get('content_hash') == hash_content(content):
                    rt_log(work_id, f'跳过已完成设定: {lore_title}')
                    chapter_count += 1
                    continue
                set_rt_state(
                    work_id, current_idx=pos, phase=f'读设定: {lore_title}',
                    active_start_idx=pos, active_end_idx=pos,
                    stream_buffer=f'正在阅读设定: {lore_title}',
                )
                upsert_chapter(
                    work_id, storage_id, idx=chapter_count,
                    title=f'[设定] {lore_title}',
                    content_hash=hash_content(content), status='processing', error='',
                )
                try:
                    prev_ctx = _build_prev_context(work_id, max_chars=6000)
                    structured = ai_read_chapter_structured(
                        settings,
                        {
                            'idx': chapter_count,
                            'id': storage_id,
                            'title': f'[设定] {lore_title}',
                            'content': content,
                        },
                        prev_context=prev_ctx,
                        on_token=lambda _tk: set_rt_state(
                            work_id, stream_buffer='模型已返回，正在写入知识库...'
                        ),
                        should_stop_fn=stop_check,
                    )
                except StoppedException:
                    upsert_chapter(work_id, storage_id, status='pending')
                    set_rt_state(work_id, status='paused', phase='已暂停')
                    return
                except Exception as e:
                    upsert_chapter(work_id, storage_id, status='failed', error=str(e)[:500])
                    rt_log(work_id, f'失败: {lore_title} ({str(e)[:100]})')
                    chapter_count += 1
                    continue
                apply_structured_result(work_id, storage_id, structured, chapter_idx=chapter_count)
                upsert_chapter(
                    work_id, storage_id, idx=chapter_count, title=f'[设定] {lore_title}',
                    status='done', summary=structured['summary'],
                    content_hash=hash_content(content), error='',
                )
                chapter_count += 1
                rt_log(work_id, f'完成 ({chapter_count}/{total}) 设定: {lore_title}')
                continue

            elif item_type == 'chapter':
                book_id = item['book_id']
                chapter_id = item['chapter_id']
                ch = _read_chapter_file(book_id, chapter_id)
                if not ch:
                    rt_log(work_id, f'跳过缺失章节: {book_id}/{chapter_id}')
                    continue

                ch_idx = item.get('chapter_idx', chapter_count)
                ch_title = item.get('chapter_title', ch.get('title', f'第{chapter_count+1}章'))
                content = ch.get('content', '')

                if _is_content_empty(content):
                    rt_log(work_id, f'跳过空章节: {ch_title}')
                    continue

                # 检查是否已处理
                storage_id = f'{book_id}::{chapter_id}'
                existing = get_chapter(work_id, storage_id)
                if existing and existing['status'] == 'done' and existing.get('content_hash') == hash_content(content):
                    rt_log(work_id, f'跳过已完成: {ch_title}')
                    chapter_count += 1
                    continue

                reread_prior = _chapter_kb_records_for_reread(work_id, storage_id) if (existing and existing['status'] == 'done') else None
                set_rt_state(work_id, current_idx=pos,
                             phase=(f'重读纠错: {ch_title}' if reread_prior else f'读: {ch_title}'),
                             active_start_idx=pos, active_end_idx=pos,
                             stream_buffer=(f'{ch_title} 有改动，带着旧笔记重读纠错…' if reread_prior else f'正在思考: {ch_title}（{book_id}）'))
                upsert_chapter(work_id, storage_id, idx=ch_idx, title=ch_title,
                              content_hash=hash_content(content), status='processing', error='')

                try:
                    prev_ctx = _build_prev_context(work_id, max_chars=6000)
                    structured = ai_read_chapter_structured(
                        settings,
                        {'idx': ch_idx, 'id': chapter_id, 'title': ch_title, 'content': content},
                        prev_context=prev_ctx, prior_records=reread_prior,
                        on_token=lambda _tk: set_rt_state(work_id, stream_buffer='模型已返回，正在写入知识库...'),
                        should_stop_fn=stop_check,
                    )
                except StoppedException:
                    upsert_chapter(work_id, storage_id, status='pending')
                    set_rt_state(work_id, status='paused', phase='已暂停',
                                 current_idx=pos, active_start_idx=-1, active_end_idx=-1,
                                 stream_buffer='通读已暂停。')
                    return
                except Exception as e:
                    if stop_check():
                        upsert_chapter(work_id, storage_id, status='pending')
                        set_rt_state(work_id, status='paused', phase='已暂停')
                        return
                    upsert_chapter(work_id, storage_id, status='failed', error=str(e)[:500])
                    rt_log(work_id, f'失败: {ch_title} ({str(e)[:100]})')
                    chapter_count += 1
                    continue

                apply_structured_result(work_id, storage_id, structured, chapter_idx=ch_idx)
                upsert_chapter(work_id, storage_id, idx=ch_idx, title=ch_title,
                              status='done',
                              summary=structured['summary'],
                              content_hash=hash_content(content),
                              error='')
                chapter_count += 1
                rt_log(work_id, f'完成 ({chapter_count}/{total}) {ch_title}')

        # 通读完成，建立索引
        set_rt_state(work_id, phase='建立索引', active_start_idx=-1, active_end_idx=-1,
                     stream_buffer='正文已经通读完，正在统一建立检索索引。')
        rt_log(work_id, '统一建立检索索引')
        incremental_embed(work_id, settings)
        render_markdown_views_fn(work_id)

        failed = [c for c in list_chapters_db(work_id) if c.get('status') == 'failed']
        if failed:
            msg = f'{len(failed)} 章失败，可点击继续重试'
            set_rt_state(work_id, status='error', phase=msg, current_idx=-1, error=msg)
            rt_log(work_id, msg)
            return

        set_rt_state(work_id, status='done', phase='完成', current_idx=-1)
        rt_log(work_id, '世界观通读完成')

    except Exception as e:
        import traceback
        err = f'世界观通读崩溃: {str(e)[:200]}'
        rt_log(work_id, err)
        set_rt_state(work_id, status='error', phase='崩溃', error=err + '\n' + traceback.format_exc()[-500:])


# ─── Phase 4: Chapter Complete ───

def do_chapter_complete(book_id, chapter_id, settings, text=None):
    m = _main()
    _read_chapter_file = m['_read_chapter_file']
    _extract_context_summary = m['_extract_context_summary']
    _is_content_empty = m['_is_content_empty']
    set_conn_meta = m['set_conn_meta']

    set_conn_meta('chapter-complete', '本章写完', book_id)
    init_db(book_id)

    raw = _read_chapter_file(book_id, chapter_id)
    if not raw:
        raise ValueError(f'章节 {chapter_id} 不存在')
    ch = {
        'id': chapter_id,
        'title': raw.get('title', ''),
        'content': text if text is not None else raw.get('content', ''),
    }

    if _is_content_empty(ch['content']):
        structured = {
            'summary': '[本章无实质正文，跳过]',
            'entities': [], 'events': [],
            'foreshadowing_new': [], 'foreshadowing_resolved': [],
            'rules': [],
        }
    else:
        prev_ctx = _build_prev_context(book_id)

        def stop_check():
            st = get_rt_state(book_id)
            return bool(st and st['pause_requested'])

        structured = ai_read_chapter_structured(
            settings, ch, prev_context=prev_ctx,
            should_stop_fn=stop_check,
        )

    apply_structured_result(book_id, chapter_id, structured)
    meta = m['get_book_meta'](book_id) or {}
    try:
        idx = meta.get('chapter_order', []).index(chapter_id)
    except ValueError:
        idx = 0
    upsert_chapter(book_id, chapter_id, idx=idx, title=ch['title'],
                  content_hash=hash_content(ch['content']),
                  summary=structured['summary'], status='done', error='')
    incremental_embed(book_id, settings)
    render_markdown_views(book_id)
    return structured['summary']


# ─── Phase 5: Q&A Context ───

def compute_smart_context_budget(ctx_len, chapter_tokens, history_tokens):
    sys_overhead = 1500
    user_msg = 500
    output_reserve = max(2000, ctx_len // 16)
    safety_margin = ctx_len // 20
    used_by_others = sys_overhead + chapter_tokens + history_tokens + user_msg + output_reserve + safety_margin
    smart_budget = ctx_len - used_by_others
    if smart_budget < 4000:
        return max(4000, smart_budget), True
    return smart_budget, False


def estimate_total_kb_size(book_id):
    cnt = 0
    for ch in list_chapters_db(book_id):
        if ch.get('status') == 'done' and ch.get('summary'):
            cnt += len(ch.get('title', '')) + len(ch.get('summary', ''))
    entities = list_entities(book_id)
    for ent in entities:
        from kb_storage import get_mentions_for_entity
        mentions = get_mentions_for_entity(book_id, ent['id'])
        for m in mentions:
            cnt += len(m['fact']) + (len(m.get('snippet', '')) or 0)
    events = list_events(book_id)
    for ev in events:
        cnt += len(ev.get('what', '')) + len(ev.get('who', ''))
    fss = list_foreshadowing(book_id)
    for f in fss:
        cnt += len(f['hint'])
    rules = list_rules(book_id)
    for r in rules:
        cnt += len(r['body'])
    return cnt


def render_entity_block(book_id, ent, max_chars=3000):
    from kb_storage import get_mentions_for_entity
    mentions = get_mentions_for_entity(book_id, ent['id'])
    aliases = json.loads(ent.get('aliases', '[]'))
    lines = [f'## {ent["canonical_name"]} ({ent["type"]})']
    if aliases:
        lines.append('别名：' + '、'.join(aliases))
    lines.append('')
    used = sum(len(l) for l in lines)
    for m in mentions:
        chapter_ref = f'第 {m["chapter_idx"] + 1} 章' if m['chapter_idx'] is not None else '?'
        block = f'### {chapter_ref}: {m.get("chapter_title", "")}\n- {m["fact"]}'
        if m.get('snippet'):
            block += f'\n  > {m["snippet"]}'
        block += '\n'
        if used + len(block) > max_chars:
            break
        lines.append(block)
        used += len(block)
    return '\n'.join(lines)


def format_events(book_id, filter=None):
    events = list_events(book_id, filter_entities=filter)
    if not events:
        return '## 时间线\n（暂无事件记录）'
    lines = ['## 时间线']
    for ev in events[:50]:
        chapter_ref = f'第 {ev["chapter_idx"] + 1} 章' if ev.get('chapter_idx') is not None else '?'
        lines.append(f'- **{chapter_ref}** ({ev.get("story_time", "")}): {ev["who"]} — {ev["what"]}')
        if ev.get("where_loc"):
            lines[-1] += f'（{ev["where_loc"]}）'
    return '\n'.join(lines)


def format_foreshadowing(book_id, status='open'):
    fss = list_foreshadowing(book_id, status=status)
    if not fss:
        return f'## 伏笔（{status}）\n（暂无记录）'
    label = '未解伏笔' if status == 'open' else '已解伏笔'
    lines = [f'## {label}']
    for f in fss:
        chapter_ref = f'第 {f["chapter_idx"] + 1} 章' if f.get('chapter_idx') is not None else '?'
        lines.append(f'- {f["hint"]}（{chapter_ref}）')
    return '\n'.join(lines)


_GBK_INITIAL_RANGES = [
    (-20319, -20284, 'A'), (-20283, -19776, 'B'), (-19775, -19219, 'C'),
    (-19218, -18711, 'D'), (-18710, -18527, 'E'), (-18526, -18240, 'F'),
    (-18239, -17923, 'G'), (-17922, -17418, 'H'), (-17417, -16475, 'J'),
    (-16474, -16213, 'K'), (-16212, -15641, 'L'), (-15640, -15166, 'M'),
    (-15165, -14923, 'N'), (-14922, -14915, 'O'), (-14914, -14631, 'P'),
    (-14630, -14150, 'Q'), (-14149, -14091, 'R'), (-14090, -13319, 'S'),
    (-13318, -12839, 'T'), (-12838, -12557, 'W'), (-12556, -11848, 'X'),
    (-11847, -11056, 'Y'), (-11055, -10247, 'Z'),
]


def _pinyin_initial(text):
    text = str(text or '').strip()
    if not text:
        return '#'
    ch = text[0]
    if ch.isascii():
        return ch.upper() if ch.isalnum() else '#'
    try:
        bs = ch.encode('gbk')
        if len(bs) >= 2:
            code = bs[0] * 256 + bs[1] - 65536
            for start, end, initial in _GBK_INITIAL_RANGES:
                if start <= code <= end:
                    return initial
    except Exception:
        pass
    return '#'


def _safe_json_loads(raw, default):
    if not raw:
        return default
    text = str(raw).strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
    text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except Exception:
        pass
    start_obj, end_obj = text.find('{'), text.rfind('}')
    start_arr, end_arr = text.find('['), text.rfind(']')
    candidates = []
    if start_obj >= 0 and end_obj > start_obj:
        candidates.append(text[start_obj:end_obj + 1])
    if start_arr >= 0 and end_arr > start_arr:
        candidates.append(text[start_arr:end_arr + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except Exception:
            continue
    return default


def chapter_outline(book_id, chapter_id):
    init_db(book_id)
    ch = get_chapter(book_id, chapter_id)
    ch = dict(ch) if ch else None
    chapter_idx = ch.get('idx') if ch else None

    mentions = get_mentions_by_chapter(book_id, chapter_id)
    by_entity = {}
    for mref in mentions:
        eid = mref.get('entity_id')
        if not eid:
            continue
        ent = by_entity.setdefault(eid, {
            'id': eid,
            'name': mref.get('canonical_name') or '',
            'type': mref.get('type') or '',
            'aliases': [],
            'initial': _pinyin_initial(mref.get('canonical_name') or ''),
            'facts': [],
            'recent_before': [],
        })
        try:
            ent['aliases'] = json.loads(mref.get('aliases') or '[]')
        except Exception:
            ent['aliases'] = []
        ent['facts'].append({
            'id': mref.get('id'),
            'fact': mref.get('fact') or '',
            'snippet': mref.get('snippet') or '',
        })

    for ent in by_entity.values():
        ent['recent_before'] = [{
            'chapter_idx': r.get('chapter_idx'),
            'chapter_title': r.get('chapter_title') or '',
            'fact': r.get('fact') or '',
            'snippet': r.get('snippet') or '',
        } for r in get_entity_recent_mentions_before(book_id, ent['id'], chapter_idx, limit=3)]

    entities = sorted(by_entity.values(), key=lambda e: (e.get('initial') or '#', e.get('name') or ''))

    rules = [{
        'id': r.get('rule_id'),
        'name': r.get('name') or '',
        'body': r.get('body') or '',
        'evidence': r.get('evidence') or '',
        'first_chapter_idx': r.get('first_chapter_idx'),
        'first_chapter_title': r.get('first_chapter_title') or '',
        'initial': _pinyin_initial(r.get('name') or ''),
    } for r in get_rule_mentions_by_chapter(book_id, chapter_id)]
    rules.sort(key=lambda r: (r.get('initial') or '#', r.get('name') or ''))

    events = get_events_by_chapter(book_id, chapter_id)
    fss = []
    for f in list_foreshadowing(book_id):
        if f.get('hint_chapter_id') == chapter_id or f.get('resolved_chapter_id') == chapter_id:
            fss.append(f)

    return {
        'chapter': ch,
        'summary': ch.get('summary', '') if ch else '',
        'entities': entities,
        'rules': rules,
        'events': events,
        'foreshadowing': fss,
        'alerts': list_consistency_alerts(book_id, chapter_id=chapter_id, status='open', limit=8),
    }


def timeline_map(book_id, focus_chapter_id=None, zoom=1):
    init_db(book_id)
    try:
        zoom = int(zoom)
    except Exception:
        zoom = 1
    zoom = max(0, min(2, zoom))
    raw_events = list_timeline_events(book_id)
    events = []
    focus_ids = []
    fallback_order = 0
    for ev in raw_events:
        fallback_order += 10
        order = ev.get('story_order')
        if order is None:
            ci = ev.get('chapter_idx')
            order = (ci if ci is not None else 9999) * 1000 + fallback_order
        importance = ev.get('importance')
        if importance is None:
            importance = 3 if ev.get('consequence') else 2
        z = ev.get('zoom_level')
        if z is None:
            z = 1 if importance >= 4 else 2
        item = dict(ev)
        item['story_order'] = order
        item['segment_id'] = item.get('segment_id') or 'main'
        item['segment_title'] = item.get('segment_title') or '主线'
        item['lane'] = item.get('lane') if item.get('lane') is not None else 0
        item['importance'] = importance
        item['zoom_level'] = z
        item['confidence'] = item.get('confidence') if item.get('confidence') is not None else 0.5
        item['timeline_status'] = item.get('timeline_status') or 'fallback'
        item['uncertain'] = item['confidence'] < 0.7 or item['timeline_status'] == 'fallback'
        if focus_chapter_id and item.get('chapter_id') == focus_chapter_id:
            focus_ids.append(item.get('id'))
        events.append(item)

    if zoom == 0:
        visible = [e for e in events if e.get('importance', 0) >= 4 or (focus_chapter_id and e.get('chapter_id') == focus_chapter_id)]
        if not visible:
            seen = set()
            visible = []
            for e in events:
                sid = e.get('segment_id') or 'main'
                if sid not in seen:
                    visible.append(e)
                    seen.add(sid)
    elif zoom == 1:
        visible = [e for e in events if e.get('importance', 0) >= 3 or (focus_chapter_id and e.get('chapter_id') == focus_chapter_id)]
    else:
        visible = events

    segments = []
    by_seg = {}
    for ev in visible:
        sid = ev.get('segment_id') or 'main'
        seg = by_seg.setdefault(sid, {
            'id': sid,
            'title': ev.get('segment_title') or sid,
            'lane': ev.get('lane') or 0,
            'events': [],
        })
        seg['events'].append(ev)
    for seg in by_seg.values():
        seg['events'].sort(key=lambda e: (e.get('story_order') or 0, e.get('chapter_idx') or 0))
        segments.append(seg)
    segments.sort(key=lambda s: (s['events'][0].get('story_order') if s['events'] else 0, s.get('lane') or 0))

    return {
        'zoom': zoom,
        'segments': segments,
        'events_total': len(events),
        'visible_total': len(visible),
        'focus_event_ids': focus_ids,
        'relations': list_timeline_relations(book_id),
        'has_ai_layout': any((e.get('timeline_status') or '') != 'fallback' for e in events),
        'uncertain_count': len([e for e in events if e.get('uncertain')]),
    }


def _timeline_event_catalog(book_id, max_events=180):
    events = list_timeline_events(book_id)
    out = []
    for ev in events[:max_events]:
        out.append({
            'id': ev.get('id'),
            'chapter': (ev.get('chapter_idx') + 1) if ev.get('chapter_idx') is not None else None,
            'chapter_title': ev.get('chapter_title') or '',
            'story_time': ev.get('story_time') or '',
            'who': ev.get('who') or '',
            'what': ev.get('what') or '',
            'where': ev.get('where_loc') or '',
            'why': ev.get('why') or '',
            'consequence': ev.get('consequence') or '',
            'previous_story_order': ev.get('story_order'),
            'previous_segment_id': ev.get('segment_id') or '',
            'previous_segment_title': ev.get('segment_title') or '',
            'previous_lane': ev.get('lane'),
            'previous_importance': ev.get('importance'),
            'previous_zoom_level': ev.get('zoom_level'),
            'previous_status': ev.get('timeline_status') or '',
        })
    return out


def fallback_timeline_arrange(book_id):
    events = list_timeline_events(book_id)
    for i, ev in enumerate(events):
        if ev.get('story_order') is not None:
            continue
        importance = 3 if ev.get('consequence') else 2
        upsert_timeline_event_meta(
            book_id, ev['id'], story_order=i * 10, segment_id='main', segment_title='叙述顺序',
            lane=0, importance=importance, zoom_level=2, confidence=0.45,
            status='fallback', reason='未经过 AI 编排，暂按章节叙述顺序显示。'
        )
    return len(events)


def arrange_timeline_ai(book_id, settings):
    m = _main()
    call_ai_full = m['call_ai_full']
    set_conn_meta = m['set_conn_meta']
    set_conn_meta('timeline', '时间线编排', book_id)
    init_db(book_id)
    catalog = _timeline_event_catalog(book_id)
    if not catalog:
        return {'updated': 0, 'fallback': True}
    if not settings or not settings.get('base_url') or not settings.get('model'):
        return {'updated': fallback_timeline_arrange(book_id), 'fallback': True}

    prompt = f"""你是小说时间线编排员。下面是数据库已经抽取好的事件事实。你的任务不是重写时间线，而是给每个事件补充可视化编排信息。

重要原则：
1. 文本出现顺序不等于故事发生顺序；遇到倒叙、插叙、回忆、梦境、谎言叙述时，必须降低 confidence，不要装作确定。
2. 保留不确定性。无法判断故事内先后时，segment_id 用 uncertain，confidence <= 0.6。
3. 不要添加数据库里没有的事件。
4. segment_id 表示连续事件段；明显时间跨度、回忆线、插叙线、平行线要拆成不同 segment。
5. story_order 越小越早。只需相对顺序，不需要真实时间戳。
6. previous_* 字段是上一次时间线的视觉风格。除非新证据明显要求变动，否则保持原来的 segment、相对间距、重要度和 lane 风格；新增事件插入合适位置，不要整体洗牌。
7. previous_status 为 user 的事件是作者手动拖动纠错过的位置，必须优先尊重；除非证据极强，不要改变它的 story_order 和 lane。

事件列表 JSON：
{json.dumps(catalog, ensure_ascii=False)}

只输出 JSON，不要代码块：
{{
  "placements": [
    {{"event_id":"事件id","story_order":10,"segment_id":"main","segment_title":"主线当前","lane":0,"importance":1-5,"zoom_level":0-2,"confidence":0.0-1.0,"reason":"为什么这么排","evidence":"依据的时间词或章节信息"}}
  ],
  "relations": [
    {{"source_event_id":"A","target_event_id":"B","relation":"before|same_time|flashback|uncertain","confidence":0.0-1.0,"evidence":"依据","note":"说明"}}
  ]
}}"""
    raw, _, err = call_ai_full(settings, [
        {'role': 'system', 'content': '你只输出严格 JSON。你承认不确定性，绝不把复杂叙事强行排成确定答案。'},
        {'role': 'user', 'content': prompt},
    ], 5000, 0.2, timeout=180)
    if err:
        return {'updated': fallback_timeline_arrange(book_id), 'fallback': True, 'error': err}
    data = _safe_json_loads(raw, {})
    placements = data.get('placements') if isinstance(data, dict) else None
    if not isinstance(placements, list):
        return {'updated': fallback_timeline_arrange(book_id), 'fallback': True, 'error': 'AI 输出无法解析'}

    valid_ids = {e['id'] for e in catalog}
    existing_by_id = {e['id']: e for e in list_timeline_events(book_id)}
    updated = 0
    for p in placements:
        eid = p.get('event_id')
        if eid not in valid_ids:
            continue
        existing = existing_by_id.get(eid) or {}
        try:
            confidence = float(p.get('confidence', 0.5))
        except Exception:
            confidence = 0.5
        try:
            importance = max(1, min(5, int(p.get('importance', 2))))
        except Exception:
            importance = 2
        try:
            zoom_level = max(0, min(2, int(p.get('zoom_level', 2))))
        except Exception:
            zoom_level = 2
        try:
            lane = int(p.get('lane', 0))
        except Exception:
            lane = 0
        try:
            story_order = int(p.get('story_order', updated * 10))
        except Exception:
            story_order = updated * 10
        status = 'ai'
        if existing.get('timeline_status') == 'user':
            if existing.get('story_order') is not None:
                story_order = existing.get('story_order')
            if existing.get('lane') is not None:
                lane = existing.get('lane')
            status = 'user'
            confidence = max(confidence, 1.0)
        if upsert_timeline_event_meta(
            book_id, eid, story_order=story_order, segment_id=p.get('segment_id') or 'main',
            segment_title=p.get('segment_title') or p.get('segment_id') or '主线',
            lane=lane, importance=importance, zoom_level=zoom_level, confidence=confidence,
            status=status, reason=p.get('reason'), evidence=p.get('evidence')
        ):
            updated += 1

    clear_ai_timeline_relations(book_id)
    relation_items = data.get('relations', []) if isinstance(data, dict) else []
    for r in relation_items:
        sid = r.get('source_event_id')
        tid = r.get('target_event_id')
        if sid not in valid_ids:
            continue
        if tid and tid not in valid_ids:
            tid = None
        try:
            conf = float(r.get('confidence', 0.5))
        except Exception:
            conf = 0.5
        add_timeline_relation(
            book_id, sid, tid, relation=r.get('relation') or 'uncertain',
            confidence=conf, status='ai', evidence=r.get('evidence'), note=r.get('note')
        )
    return {'updated': updated, 'fallback': False}


def generate_short_prediction(book_id, settings):
    m = _main()
    call_ai_full = m['call_ai_full']
    save_prediction_md = m['save_prediction_md']
    set_conn_meta = m['set_conn_meta']
    set_conn_meta('prediction', '预言更新', book_id)
    if not settings or not settings.get('base_url') or not settings.get('model'):
        return '', '请先配置API'
    chapters = [c for c in list_chapters_db(book_id) if c.get('status') == 'done' and c.get('summary')]
    latest = chapters[-6:]
    notes = []
    for ch in latest:
        notes.append(f"第 {ch.get('idx', 0) + 1} 章《{ch.get('title','')}》：{ch.get('summary','')[:800]}")
    open_fs = list_foreshadowing(book_id, status='open')[:20]
    fs_text = '\n'.join([f"- 第 {(f.get('chapter_idx') or 0) + 1} 章：{f.get('hint','')}" for f in open_fs]) or '（暂无）'
    prompt = f"""你是正在追读这部小说的读者，只基于已公开到最新章节的信息写一个很短的预言。

最近章节笔记：
{chr(10).join(notes)}

未解伏笔：
{fs_text}

要求：
- 第一人称，用“我觉得……”
- 只预测接下来最可能的一小段发展，不写长评
- 允许不确定，不要编造数据库外事实
- 120-260 字，纯文本，不要标题"""
    result, reasoning, err = call_ai_full(settings, [
        {'role': 'system', 'content': '你是克制的追更读者。短、具体、承认不确定性。'},
        {'role': 'user', 'content': prompt},
    ], 600, 0.65, timeout=120)
    if err:
        return '', err
    result = (result or '').strip()
    save_prediction_md(book_id, result)
    return result, None


def consistency_check(book_id, chapter_id, text, settings):
    m = _main()
    call_ai_full = m['call_ai_full']
    set_conn_meta = m['set_conn_meta']
    set_conn_meta('auto_comment', '吃书雷达', book_id)
    init_db(book_id)
    if not settings or not settings.get('base_url') or not settings.get('model'):
        return {'alerts': [], 'error': '请先配置API'}
    text = (text or '').strip()
    if len(text) < 80:
        return {'alerts': []}
    focus_text = text[-2200:]
    matched = match_entities_by_name(book_id, focus_text)
    matched_names = [e.get('canonical_name') for e in matched[:12]]
    ctx_parts = []
    if matched:
        for ent in matched[:8]:
            block = render_entity_block(book_id, ent, max_chars=1200)
            ctx_parts.append(block)
    current = chapter_outline(book_id, chapter_id)
    if current.get('events'):
        ctx_parts.append('## 本章已入库事件\n' + '\n'.join([f"- {e.get('story_time','')} {e.get('who','')}：{e.get('what','')}" for e in current['events'][:8]]))
    rules = list_rules(book_id)[:30]
    if rules:
        ctx_parts.append('## 已确认/已记录规则\n' + '\n'.join([f"- {r.get('name')}: {r.get('body')[:180]}" for r in rules]))
    tl = timeline_map(book_id, focus_chapter_id=chapter_id, zoom=1)
    focus_tl = []
    for seg in tl.get('segments', [])[:8]:
        for ev in seg.get('events', [])[:8]:
            focus_tl.append(f"- 顺序{ev.get('story_order')} 第{(ev.get('chapter_idx') or 0)+1}章 {ev.get('story_time','')}: {ev.get('what','')}")
    if focus_tl:
        ctx_parts.append('## 时间线参考\n' + '\n'.join(focus_tl[:30]))
    context = '\n\n'.join(ctx_parts)
    if len(context) > 9000:
        context = context[:9000]
    prompt = f"""你是小说写作时的“吃书雷达”。你只提醒可能冲突，不替作者裁判，不修改数据库。

相关知识库：
{context or '（知识库很少）'}

作者当前正在写的最新文本：
{focus_text}

请检查是否存在“可能吃书/设定冲突/时间线冲突/人物状态冲突”。如果可能是倒叙、回忆、梦境、误导叙述、角色谎言，请把它列为可解释情况，不要断言作者写错。

只输出 JSON：
{{"alerts":[{{"kind":"timeline|character|rule|object|continuity","severity":"low|medium|high","message":"一句话提醒，语气谦逊","evidence":"知识库依据","suggestion":"建议作者怎么处理或标记","highlight_text":"作者最新文本中需要用荧光笔标出的原文短句，必须逐字存在于最新文本"}}]}}

最多 3 条。没有明显问题输出 {{"alerts":[]}}。highlight_text 要尽量短，优先选择新增文本里直接造成冲突的句子；如果找不到精确原文，留空。"""
    raw, _, err = call_ai_full(settings, [
        {'role': 'system', 'content': '你是谨慎的小说连续性检查器。只输出严格 JSON，不要替作者下定论。'},
        {'role': 'user', 'content': prompt},
    ], 1200, 0.2, timeout=90)
    if err:
        return {'alerts': [], 'error': err}
    data = _safe_json_loads(raw, {'alerts': []})
    alerts = data.get('alerts') if isinstance(data, dict) else []
    if not isinstance(alerts, list):
        alerts = []
    clean = []
    for a in alerts[:3]:
        if not isinstance(a, dict) or not str(a.get('message') or '').strip():
            continue
        clean.append({
            'kind': str(a.get('kind') or 'continuity')[:40],
            'severity': str(a.get('severity') or 'medium')[:20],
            'message': str(a.get('message') or '').strip()[:300],
            'evidence': str(a.get('evidence') or '').strip()[:500],
            'suggestion': str(a.get('suggestion') or '').strip()[:300],
            'highlight_text': str(a.get('highlight_text') or '').strip()[:240],
        })
    source_hash = hashlib.sha256(focus_text.encode('utf-8')).hexdigest()
    saved = save_consistency_alerts(book_id, chapter_id, clean, source_hash=source_hash) if clean else []
    return {'alerts': saved, 'matched_entities': matched_names}


def consistency_deep_check(book_id, alert_id, settings):
    m = _lazy_main()
    call_ai_full = m['call_ai_full']
    _read_chapter_file = m['_read_chapter_file']
    set_conn_meta = m['set_conn_meta']
    set_conn_meta('auto_comment', '吃书深度确认', book_id)
    init_db(book_id)
    if not settings or not settings.get('base_url') or not settings.get('model'):
        return {'error': '请先配置API'}
    alerts = list_consistency_alerts(book_id, status=None, limit=50)
    alert = None
    for a in alerts:
        if a['id'] == alert_id:
            alert = a
            break
    if not alert:
        return {'error': '找不到该提醒'}
    cid = alert.get('chapter_id') or ''
    ctx_parts = []
    ctx_parts.append(f'## 雷达提醒\n- 类型：{alert.get("kind","")}\n- 严重度：{alert.get("severity","")}\n- 提醒内容：{alert.get("message","")}\n- 知识库依据：{alert.get("evidence","")}\n- 建议：{alert.get("suggestion","")}')
    if cid:
        ch = _read_chapter_file(book_id, cid)
        if ch and ch.get('content'):
            content = ch['content']
            if len(content) > 6000:
                content = content[-6000:]
            ch_idx = '?'
            meta = m['get_book_meta'](book_id) or {}
            order = meta.get('chapter_order', [])
            if cid in order:
                ch_idx = order.index(cid) + 1
            ctx_parts.append(f'## 当前章节（第{ch_idx}章：{ch.get("title","")}）原文\n{content}')
        summary_fn = m.get('_extract_context_summary')
        get_source = m.get('get_source')
        if summary_fn and get_source:
            source_text = get_source(book_id)
            if source_text:
                s = summary_fn(source_text)
                if s:
                    ctx_parts.append(f'## 前文摘要\n{s}')
    matched = match_entities_by_name(book_id, alert.get('message', '') + ' ' + alert.get('evidence', ''))
    if matched:
        for ent in matched[:6]:
            block = render_entity_block(book_id, ent, max_chars=1500)
            ctx_parts.append(block)
    rules = list_rules(book_id)[:20]
    if rules:
        ctx_parts.append('## 已确认规则\n' + '\n'.join([f"- {r.get('name')}: {r.get('body')[:200]}" for r in rules]))
    tl = timeline_map(book_id, zoom=1)
    focus_tl = []
    for seg in tl.get('segments', [])[:6]:
        for ev in seg.get('events', [])[:6]:
            focus_tl.append(f"- 第{(ev.get('chapter_idx') or 0)+1}章 {ev.get('story_time','')}: {ev.get('what','')}")
    if focus_tl:
        ctx_parts.append('## 时间线参考\n' + '\n'.join(focus_tl[:25]))
    if cid:
        current = chapter_outline(book_id, cid)
        if current.get('events'):
            ctx_parts.append('## 本章已入库事件\n' + '\n'.join([f"- {e.get('story_time','')} {e.get('who','')}：{e.get('what','')}" for e in current['events'][:8]]))
    context = '\n\n'.join(ctx_parts)
    if len(context) > 14000:
        context = context[:14000]
    prompt = f"""你是小说写作搭档 Luca，正在帮作者深入分析一个"可能吃书"的提醒。

相关知识库和原文：
{context}

请你深入分析这个矛盾点，把来龙去脉跟作者说清楚。要求：
1. 找出具体冲突在哪里——哪些设定/描述前后不一致
2. 引用原文出处——必须标注「第X章」并引用原文关键句子，用 > 引用格式
3. 分析可能的原因——是作者写错了，还是倒叙/回忆/梦境/角色谎言等可解释情况
4. 给出明确建议——如果确实吃书，建议怎么修；如果可以解释，说明理由

语气：你是搭档不是裁判，用讨论的口吻，不要下定论。"""
    raw, _, err = call_ai_full(settings, [
        {'role': 'system', 'content': '你是小说写作搭档，帮作者深入分析设定矛盾。必须引用原文出处（章节号+原文片段）。'},
        {'role': 'user', 'content': prompt},
    ], 2000, 0.3, timeout=120)
    if err:
        return {'error': err}
    return {'analysis': raw, 'alert_id': alert_id}


def _focus_passages(content, focus_texts=None, max_chars=6000):
    content = content or ''
    focus_texts = [str(x).strip() for x in (focus_texts or []) if str(x).strip()]
    if not content:
        return ''
    if not focus_texts:
        return content[:max_chars] if len(content) <= max_chars else content[-max_chars:]

    spans = []
    for ft in focus_texts:
        idx = content.find(ft)
        if idx < 0:
            compact = re.sub(r'\s+', '', ft)
            compact_content = re.sub(r'\s+', '', content)
            ci = compact_content.find(compact[:80])
            if ci >= 0:
                idx = max(0, min(len(content) - 1, ci))
        if idx >= 0:
            start = content.rfind('\n\n', 0, idx)
            if start < 0:
                start = content.rfind('\n', 0, idx)
            start = 0 if start < 0 else start + 1
            end = content.find('\n\n', idx + len(ft))
            if end < 0:
                end = content.find('\n', idx + len(ft))
            end = len(content) if end < 0 else end
            start = max(0, start - 300)
            end = min(len(content), end + 300)
            spans.append((start, end))

    if not spans:
        joined = '\n'.join(focus_texts)
        return (joined + '\n\n--- 原章片段兜底 ---\n' + content[:max_chars])[:max_chars]

    spans.sort()
    merged = []
    for s, e in spans:
        if not merged or s > merged[-1][1] + 80:
            merged.append([s, e])
        else:
            merged[-1][1] = max(merged[-1][1], e)
    parts = [content[s:e].strip() for s, e in merged if content[s:e].strip()]
    text = '\n\n---\n\n'.join(parts)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def _chapter_kb_records_for_reread(book_id, chapter_id):
    records = {'mentions': [], 'events': [], 'rules': [], 'foreshadowing': []}
    for mref in get_mentions_by_chapter(book_id, chapter_id):
        records['mentions'].append({
            'id': mref.get('id'),
            'entity': mref.get('canonical_name') or '',
            'type': mref.get('type') or '',
            'fact': mref.get('fact') or '',
            'snippet': mref.get('snippet') or '',
        })
    for ev in get_events_by_chapter(book_id, chapter_id):
        records['events'].append({
            'id': ev.get('id'),
            'story_time': ev.get('story_time') or '',
            'who': ev.get('who') or '',
            'what': ev.get('what') or '',
            'where': ev.get('where_loc') or '',
            'why': ev.get('why') or '',
            'consequence': ev.get('consequence') or '',
        })
    for r in get_rule_mentions_by_chapter(book_id, chapter_id):
        records['rules'].append({
            'id': r.get('rule_id'),
            'name': r.get('name') or '',
            'body': r.get('body') or '',
            'evidence': r.get('evidence') or '',
        })
    for f in list_foreshadowing(book_id):
        if f.get('hint_chapter_id') == chapter_id or f.get('resolved_chapter_id') == chapter_id:
            records['foreshadowing'].append({
                'id': f.get('id'),
                'status': f.get('status') or '',
                'hint': f.get('hint') or '',
                'resolution': f.get('resolution') or '',
            })
    return records


def apply_partial_structured_result(book_id, chapter_id, structured):
    added = {'mentions': 0, 'events': 0, 'foreshadowing': 0, 'rules': 0}
    for ent_data in structured.get('entities', []) or []:
        canonical = str(ent_data.get('canonical_name', '')).strip()
        if not canonical:
            continue
        ent_id = upsert_entity(
            book_id, canonical, ent_data.get('type', '人物') or '人物',
            aliases=ent_data.get('aliases_in_chapter', []) or [],
            first_chapter_id=chapter_id,
        )
        for fact_data in ent_data.get('facts', []) or []:
            fact = str(fact_data.get('fact', '')).strip()
            if not fact:
                continue
            add_mention(book_id, ent_id, chapter_id, fact=fact, snippet=fact_data.get('snippet'))
            added['mentions'] += 1

    for ev_data in structured.get('events', []) or []:
        what = str(ev_data.get('what', '')).strip()
        if not what:
            continue
        add_event(
            book_id, chapter_id,
            story_time=ev_data.get('story_time', ''),
            who=ev_data.get('who', ''),
            what=what,
            where_loc=ev_data.get('where', ev_data.get('where_loc', '')),
            why=ev_data.get('why', ''),
            consequence=ev_data.get('consequence', ''),
        )
        added['events'] += 1

    for fs_data in structured.get('foreshadowing_new', []) or []:
        hint = str(fs_data.get('hint', '')).strip()
        if hint:
            add_foreshadowing(book_id, chapter_id, hint=hint)
            added['foreshadowing'] += 1

    for fs_res_data in structured.get('foreshadowing_resolved', []) or []:
        earlier = str(fs_res_data.get('earlier_hint', '')).strip()
        if earlier:
            resolve_foreshadowing(book_id, hint=earlier, resolved_chapter_id=chapter_id,
                                  resolution=fs_res_data.get('resolution', ''))
            added['foreshadowing'] += 1

    for rule_data in structured.get('rules', []) or []:
        name = str(rule_data.get('name', '')).strip()
        body = str(rule_data.get('body', '')).strip()
        if name and body:
            rid = upsert_rule(book_id, name, body=body, first_chapter_id=chapter_id)
            add_rule_mention(book_id, rid, chapter_id, evidence=rule_data.get('snippet') or body[:200])
            added['rules'] += 1
    return added


def reread_passages(book_id, chapter_ids, correction, focus_texts, settings):
    m = _main()
    _read_chapter_file = m['_read_chapter_file']
    call_ai_full = m['call_ai_full']
    set_conn_meta = m['set_conn_meta']
    render_markdown_views_fn = render_markdown_views
    set_conn_meta('kb-reread', '局部重读', book_id)
    init_db(book_id)

    chapter_ids = [str(x) for x in (chapter_ids or []) if str(x)]
    focus_texts = [str(x) for x in (focus_texts or []) if str(x).strip()]
    correction = str(correction or '').strip()
    if not chapter_ids:
        raise ValueError('缺少要重读的章节')
    if not correction:
        raise ValueError('缺少用户纠正说明')
    if not settings or not settings.get('base_url') or not settings.get('model'):
        raise ValueError('请先配置API')

    total_deleted = {'mentions': 0, 'events': 0, 'foreshadowing': 0, 'rules': 0}
    total_added = {'mentions': 0, 'events': 0, 'foreshadowing': 0, 'rules': 0}
    details = []
    events_changed = False

    for chapter_id in chapter_ids[:6]:
        raw = _read_chapter_file(book_id, chapter_id)
        if not raw:
            continue
        title = raw.get('title', '')
        content = raw.get('content', '')
        passages = _focus_passages(content, focus_texts, max_chars=6500)
        current_records = _chapter_kb_records_for_reread(book_id, chapter_id)
        prompt = f"""用户指出 Luca 对某些段落的理解有误。你要局部重读，修正知识库。

用户纠正说明：
{correction}

重读章节：《{title}》

只阅读以下有关段落，不要扩大到无关正文：
{passages}

当前知识库里与本章有关的记录（可能有错，id 很重要）：
{json.dumps(current_records, ensure_ascii=False)}

任务：
1. 根据用户纠正和原文段落，判断哪些旧记录是由误读造成的，列入 delete。
2. 用通读同样的结构补充正确记录，列入 add。
3. 只处理这段相关内容。不要删除或改写无关记录。
4. 如果不确定，不要乱删；可以少改。
5. snippet 必须来自原文段落。

只输出严格 JSON，不要代码块：
{{
  "delete": {{"mentions":["旧mention id"],"events":["旧event id"],"rules":["旧rule id"],"foreshadowing":["旧foreshadowing id"]}},
  "add": {{
    "entities":[{{"canonical_name":"人物/实体名","type":"该实体的类别，自由词。常见：人物/势力/地点/物品/概念/种族/功法/组织…但不限于此，按本作世界观自拟","aliases_in_chapter":[],"facts":[{{"fact":"正确事实","snippet":"原文片段"}}]}}],
    "events":[{{"story_time":"故事内时间，不能确定就写时间未定","who":"参与者","what":"正确事件","where":"地点","why":"原因","consequence":"后果","snippet":"原文片段"}}],
    "foreshadowing_new":[{{"hint":"新伏笔","snippet":"原文片段"}}],
    "foreshadowing_resolved":[{{"earlier_hint":"被回收的旧伏笔","resolution":"如何回收","snippet":"原文片段"}}],
    "rules":[{{"name":"设定名","body":"正确设定","snippet":"原文片段"}}]
  }},
  "note":"一句话说明你改了什么"
}}"""
        raw_result, _, err = call_ai_full(settings, [
            {'role': 'system', 'content': '你是严谨的知识库局部重读器。只输出严格 JSON。只修正用户指出的误读，不碰无关内容。警告：你已有的知识库信息可能是错的，暂时抛弃它们，严格基于原文段落重新判断。'},
            {'role': 'user', 'content': prompt},
        ], 4000, 0.2, timeout=180)
        if err:
            raise RuntimeError(err)
        patch = _safe_json_loads(raw_result, {})
        if not isinstance(patch, dict):
            raise RuntimeError('局部重读输出无法解析')
        delete_map = patch.get('delete') if isinstance(patch.get('delete'), dict) else {}
        add_structured = patch.get('add') if isinstance(patch.get('add'), dict) else {}
        deleted = delete_kb_records(book_id, delete_map)
        added = apply_partial_structured_result(book_id, chapter_id, add_structured)
        for k, v in deleted.items():
            total_deleted[k] = total_deleted.get(k, 0) + (v or 0)
        for k, v in added.items():
            total_added[k] = total_added.get(k, 0) + (v or 0)
        if deleted.get('events') or added.get('events'):
            events_changed = True
        details.append({'chapter_id': chapter_id, 'title': title, 'deleted': deleted, 'added': added, 'note': patch.get('note', '')})

    if not details:
        raise ValueError('没有找到可重读的章节')

    try:
        embed_clear(book_id)
        incremental_embed(book_id, settings)
    except Exception:
        pass
    try:
        render_markdown_views_fn(book_id)
    except Exception:
        pass
    if events_changed:
        try:
            arrange_timeline_ai(book_id, settings)
        except Exception:
            pass

    return {
        'chapters': len(details),
        'details': details,
        'deleted': total_deleted,
        'added': total_added,
        'events_changed': events_changed,
    }


def render_chapter_block(ch, max_chars=2500):
    idx = ch.get('idx')
    chapter_ref = f'第 {idx + 1} 章' if idx is not None else '章节'
    text = f'## {chapter_ref}: {ch.get("title", "")}\n{ch.get("summary", "")}'
    if len(text) > max_chars:
        text = text[:max_chars - 3] + '...'
    return text


def qa_context(book_id, user_query='', settings=None, chapter_tokens=0, history_tokens=0):
    m = _main()
    _get_effective_context_length = m['_get_effective_context_length']
    init_db(book_id)

    ctx_len = _get_effective_context_length(settings)
    budget, need_compress = compute_smart_context_budget(ctx_len, chapter_tokens, history_tokens)
    total_kb = estimate_total_kb_size(book_id)
    budget = min(budget, total_kb + 1000)

    if budget < 1000:
        return '（知识库数据较少，正在积累中）', need_compress

    parts = []
    backend = None
    if user_query:
        try:
            backend = get_embedding_backend(settings)
        except Exception:
            backend = None

    matched_entities = match_entities_by_name(book_id, user_query) if user_query else []
    matched_ids = [e['id'] for e in matched_entities]

    vector_hits = []
    if user_query.strip() and backend:
        top_k = 8 if ctx_len < 32000 else 15 if ctx_len < 128000 else 30
        vector_hits = embed_query(book_id, user_query, backend, top_k=top_k)

    if re.search(r'第\s*\d+\s*章|什么时候|时间线|顺序|经过', user_query):
        parts.append(format_events(book_id, filter=[e['canonical_name'] for e in matched_entities] if matched_entities else None))

    if re.search(r'伏笔|悬念|线索|未解|铺垫', user_query):
        parts.append(format_foreshadowing(book_id, status='open'))

    used = sum(len(p) for p in parts)
    remaining = budget - used
    per_entity_cap = 3000 if ctx_len < 64000 else 8000 if ctx_len < 256000 else 20000

    candidate_entity_ids = set(matched_ids)
    candidate_chapter_ids = set()
    for hit in vector_hits:
        meta = hit.get('metadata', {})
        if meta.get('source_type') == 'entity' and meta.get('source_id'):
            candidate_entity_ids.add(meta['source_id'])
        if meta.get('source_type') == 'chapter_summary' and meta.get('source_id'):
            candidate_chapter_ids.add(meta['source_id'])

    for n in re.findall(r'第\s*(\d+)\s*章', user_query or ''):
        try:
            idx = int(n) - 1
            for ch in list_chapters_db(book_id):
                if ch.get('idx') == idx:
                    candidate_chapter_ids.add(ch['id'])
                    break
        except Exception:
            pass

    all_chapters = [c for c in list_chapters_db(book_id) if c.get('status') == 'done' and c.get('summary')]
    candidate_chapters = [c for c in all_chapters if c['id'] in candidate_chapter_ids]
    remaining_chapters = [c for c in all_chapters if c['id'] not in candidate_chapter_ids]

    per_chapter_cap = 1800 if ctx_len < 64000 else 3200 if ctx_len < 256000 else 8000
    for ch in candidate_chapters + remaining_chapters:
        if remaining <= 0:
            break
        block = render_chapter_block(ch, max_chars=min(remaining, per_chapter_cap))
        parts.append(block)
        remaining -= len(block)

    all_entities = list_entities(book_id)
    candidate_entities = [e for e in all_entities if e['id'] in candidate_entity_ids]
    remaining_ents = [e for e in all_entities if e['id'] not in candidate_entity_ids]

    for ent in candidate_entities + remaining_ents:
        if remaining <= 0:
            break
        block = render_entity_block(book_id, ent, max_chars=min(remaining, per_entity_cap))
        parts.append(block)
        remaining -= len(block)

    return '\n\n---\n\n'.join(parts), need_compress
