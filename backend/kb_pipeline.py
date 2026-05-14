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
    add_event, list_events,
    add_foreshadowing, resolve_foreshadowing, list_foreshadowing,
    upsert_rule, list_rules,
    set_rt_state, get_rt_state, get_pause_requested, append_stream, rt_log, get_rt_logs,
    embed_upsert, embed_query, embed_clear, embed_collection_count,
    hash_content, get_embedding_backend_id, get_embedding_chunk, get_kb_path,
    _get_conn,
)
from embeddings import get_embedding_backend


def _lazy_main():
    from main import (
        call_ai_full, call_ai_stream,
        _get_effective_context_length,
        _read_chapter_file, get_book_meta, get_book_dir,
        save_source, _get_source_dir, _write_entity_file,
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
JSON 必须能被 Python 的 json.loads 解析，不要包含任何多余文字。'''

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


def _est_tokens(text: str) -> int:
    return int(len(text or '') * 0.55)


def _compute_output_budget(ctx_len: int, input_chars: int, prev_context: str) -> int:
    if ctx_len <= 0:
        return 8192
    input_tokens = _est_tokens(prev_context) + int(input_chars * 0.55) + SYSTEM_OVERHEAD_TOKENS
    remaining = ctx_len - input_tokens - SAFETY_TOKENS
    return max(4096, remaining)


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


def ai_read_chapter_structured(settings, ch, prev_context='', on_token=None, should_stop_fn=None):
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
6. 只输出 JSON 数组，不要任何多余文字、注释、代码块标记。'''

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
            book_id, canonical, ent_data.get('type', '人物') or '人物',
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
            upsert_rule(book_id, name, body=body, first_chapter_id=chapter_id)


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
    from kb_storage import _get_chroma_client, _ChromaEmbeddingFunc
    import chromadb

    ef = _ChromaEmbeddingFunc(backend)
    client = _get_chroma_client(book_id)
    try:
        collection = client.get_collection(name='luca_kb', embedding_function=ef)
    except:
        collection = client.create_collection(name='luca_kb', embedding_function=ef)

    texts_to_embed = []
    ids_to_embed = []
    metadatas_to_embed = []
    hashes_to_register = []

    def queue_chunk(chunk_id, text, source_type, source_id):
        content_hash = hash_content(text)
        old = get_embedding_chunk(book_id, chunk_id)
        if old and old.get('content_hash') == content_hash and old.get('backend_id') == backend.backend_id:
            return
        texts_to_embed.append(text)
        ids_to_embed.append(chunk_id)
        metadatas_to_embed.append({'source_type': source_type, 'source_id': source_id})
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
        queue_chunk(cid, text, 'entity', ent['id'])

    events = list_events(book_id)
    if events:
        ev_text = '事件时间线:\n'
        for ev in events[:100]:
            ev_text += f'- 第{ev.get("chapter_idx","?")}章: {ev["who"]} {ev["what"]}\n'
        cid = 'events_all'
        queue_chunk(cid, ev_text, 'events', 'all')

    fss = list_foreshadowing(book_id)
    if fss:
        fs_text = '伏笔:\n'
        for f in fss:
            status = '未解' if f['status'] == 'open' else '已解'
            fs_text += f'- [{status}] {f["hint"]}\n'
        cid = 'foreshadowing_all'
        queue_chunk(cid, fs_text, 'foreshadowing', 'all')

    rules = list_rules(book_id)
    if rules:
        r_text = '世界观规则:\n'
        for r in rules:
            r_text += f'- {r["name"]}: {r["body"]}\n'
        cid = 'rules_all'
        queue_chunk(cid, r_text, 'rules', 'all')

    if texts_to_embed:
        vecs = backend.embed(texts_to_embed)
        collection.upsert(ids=ids_to_embed, embeddings=vecs,
                          documents=texts_to_embed, metadatas=metadatas_to_embed)
        from kb_storage import register_embedding_chunk
        for chunk_id, source_type, source_id, content_hash in hashes_to_register:
            register_embedding_chunk(book_id, chunk_id, source_type, source_id,
                                     content_hash, backend.backend_id)


# ─── Phase 3: Readthrough Orchestrator ───

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

        def stop_check():
            st = get_rt_state(book_id)
            return bool(st and st['pause_requested'])

        current_max_batch = max_batch_chapters

        def _unchanged_done(ch):
            existing = get_chapter(book_id, ch['id'])
            return bool(existing and existing['status'] == 'done' and existing['content_hash'] == hash_content(ch['content']))

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

            set_rt_state(book_id, current_idx=ch['idx'], phase=f'读: {ch["title"]}',
                         active_start_idx=ch['idx'], active_end_idx=ch['idx'],
                         stream_buffer=f'正在思考第 {ch["idx"] + 1} 章：{ch["title"]}')
            upsert_chapter(book_id, ch['id'], idx=ch['idx'], title=ch['title'],
                          content_hash=hash_content(ch['content']), status='processing', error='')

            try:
                prev_ctx = _build_prev_context(book_id, max_chars=_prev_context_limit(read_context_window))
                structured = ai_read_chapter_structured(
                    settings, ch, prev_context=prev_ctx,
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
            if use_batch and current_max_batch > 1:
                used = _chapter_input_cost(ch)
                scan = pos + 1
                while scan < len(chapters) and len(batch) < current_max_batch:
                    nxt = chapters[scan]
                    if stop_check() or _unchanged_done(nxt) or _is_content_empty(nxt['content']):
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
