#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chat_json_to_md.py
将 GitHub Copilot Chat / VS Code 会话 JSON (version=3) 转换为 Markdown。

新增功能:
1) 支持输入为目录: 递归查找 *.json (version=3) 并批量转换
2) 支持缺省不传输入路径: 默认使用当前工作目录 (脚本运行所在目录)
3) 每条消息输出请求时间戳 (若 JSON 中含 request.timestamp; 毫秒 → 本地时间与 ISO8601 UTC)
4) 统计代码块数量 / 行数 / 语言分布, 以及用户与助手消息数
5) 汇总写入每个 Markdown 顶部的统计区块
6) 自动跳过无法解析或 version 不等于 3 的 JSON (输出告警)

使用示例:
    # 单文件
    python chat_json_to_md.py session.json
    # 指定输出文件
    python chat_json_to_md.py session.json -o session.md
    # 目录批量 (输出与原 JSON 同目录同名 .md)
    python chat_json_to_md.py path/to/folder
    # 不带参数: 等价于 python chat_json_to_md.py .
    python chat_json_to_md.py

统计说明:
    code_blocks: 使用 ``` 包围的片段数量
    code_block_lines: 所有代码块内部行总数 (去掉开始/结束分隔行)
    code_langs: 代码块开头三反引号后紧跟的语言标识（不区分大小写）频次

限制:
    - 仅适配 version==3 结构
    - timestamp 猜测为毫秒; 若实际为秒且需强制可后续扩展
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import re
import shutil
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Set, Callable
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

Line = Dict[str, Any]

def load_json(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def is_chat_session_json(data: Dict[str, Any]) -> bool:
    """判定是否为聊天会话 JSON:
    条件:
      - version == 3
      - 存在 requests 列表
      - 列表中至少有一个元素包含 message 或 response
    """
    if not isinstance(data, dict):
        return False
    if data.get('version') != 3:
        return False
    reqs = data.get('requests')
    if not isinstance(reqs, list) or not reqs:
        return False
    for r in reqs:
        if isinstance(r, dict) and ('message' in r or 'response' in r):
            return True
    return False

def extract_text_from_message(msg: Dict[str, Any]) -> str:
    if not msg:
        return ''
    txt = msg.get('text')
    if txt:
        return txt.strip()
    parts = msg.get('parts') or []
    collected = []
    for p in parts:
        t = p.get('text') if isinstance(p, dict) else None
        if t:
            collected.append(t)
    return '\n'.join(collected).strip()

def sanitize_markdown(text: str) -> str:
    if not text:
        return ''
    # 规范换行: 去掉过多空行(>3连)为2行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.rstrip()

def _format_timestamp(ms: Optional[int]) -> Tuple[str, str]:
    """毫秒(或秒)时间戳 -> (本地时间, UTC ISO8601)"""
    if ms is None:
        return '', ''
    if ms < 10_000_000_000:  # 应对可能的秒级时间戳
        ms *= 1000
    dt_utc = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    local_dt = dt_utc.astimezone()
    return local_dt.strftime('%Y-%m-%d %H:%M:%S %Z'), dt_utc.isoformat()


def _decode_file_uri(uri: str) -> str:
    # 形如 file:///d%3A/path/to/file -> /d:/path/to/file 或 d:/path/to/file
    if not uri.lower().startswith('file:'):
        return uri
    # 去掉 file:// 前缀
    path_part = uri.split('://', 1)[-1]
    path_part = urllib.parse.unquote(path_part)
    # Windows 常见会多一个前导 /
    if re.match(r'^/[A-Za-z]:', path_part):
        path_part = path_part[1:]
    return path_part


def extract_messages(data: Dict[str, Any]) -> List[Line]:
    lines: List[Line] = []
    requests = data.get('requests') or []
    for req_index, req in enumerate(requests, start=1):
        request_id = req.get('requestId')
        message = req.get('message') or {}
        local_ts, utc_ts = _format_timestamp(req.get('timestamp'))
        # 采集 variableData 中的文件引用（id/name/fullName）
        file_refs: List[Dict[str, str]] = []
        var_data = req.get('variableData') or {}
        variables = var_data.get('variables') if isinstance(var_data, dict) else []
        if isinstance(variables, list):
            for v in variables:
                if isinstance(v, dict):
                    raw_id = v.get('id') or ''
                    full_name = v.get('fullName') or ''
                    name = v.get('name') or ''
                    # 解析路径优先级: fullName > file:// id > name(若是路径)
                    candidate_path = ''
                    if isinstance(full_name, str) and full_name:
                        candidate_path = full_name
                    elif isinstance(raw_id, str) and raw_id.lower().startswith('file:'):
                        candidate_path = _decode_file_uri(raw_id)
                    elif name and re.search(r'[\\/]', name):
                        candidate_path = name
                    display = name or os.path.basename(candidate_path) or raw_id or 'file'
                    if candidate_path:
                        file_refs.append({'display': display, 'path': candidate_path})
        user_text = extract_text_from_message(message)
        if user_text:
            lines.append({
                'role': 'user',
                'content': sanitize_markdown(user_text),
                'requestId': request_id,
                'requestIndex': req_index,
                'localTime': local_ts,
                'utcTime': utc_ts,
                'fileRefs': file_refs,
            })
        # responses
        for resp in req.get('response') or []:
            if isinstance(resp, dict) and 'value' in resp:
                val = resp.get('value')
                if not isinstance(val, str):
                    continue
                cleaned = sanitize_markdown(val)
                if cleaned:
                    lines.append({
                        'role': 'assistant',
                        'content': cleaned,
                        'requestId': request_id,
                        'requestIndex': req_index,
                        'localTime': local_ts,
                        'utcTime': utc_ts,
                        'fileRefs': [],  # 响应侧不解析 variableData
                    })
    return lines

def _collect_code_stats(lines: List[Line]) -> Dict[str, Any]:
    code_block_pattern = re.compile(r"```(.*?)```", re.DOTALL)
    lang_counter: Dict[str, int] = {}
    code_blocks = 0
    code_block_lines = 0
    for ln in lines:
        content = ln['content']
        for m in code_block_pattern.finditer(content):
            code_blocks += 1
            block = m.group(0)
            first_line = block.split('\n', 1)[0]
            lang = first_line.strip('`').strip().lower()
            if lang:
                lang_counter[lang] = lang_counter.get(lang, 0) + 1
            inner_lines = block.strip().split('\n')
            if len(inner_lines) >= 2:
                code_block_lines += max(0, len(inner_lines) - 2)
    return {
        'code_blocks': code_blocks,
        'code_block_lines': code_block_lines,
        'code_langs': lang_counter,
    }


def render_markdown(data: Dict[str, Any], lines: List[Line], embed_ctx: Optional[Dict[str, Any]] = None) -> str:
    session_id = data.get('sessionId', 'unknown-session')
    requester = data.get('requesterUsername') or data.get('requester', {}).get('username') or 'user'
    responder = data.get('responderUsername') or data.get('responder', {}).get('username') or 'assistant'
    user_count = sum(1 for l in lines if l['role'] == 'user')
    assistant_count = sum(1 for l in lines if l['role'] == 'assistant')
    stats = _collect_code_stats(lines)
    header = [
        f"# 会话记录 {session_id}",
        '',
        '## 元数据',
        '',
        f'- 发起者: `{requester}`',
        f'- 响应者: `{responder}`',
        f'- 条目总数: {len(lines)}',
        f'- 用户消息数: {user_count}',
        f'- 助手消息数: {assistant_count}',
        f"- 代码块数量: {stats['code_blocks']}",
        f"- 代码块行数: {stats['code_block_lines']}",
    ]
    if stats['code_langs']:
        langs_sorted = sorted(stats['code_langs'].items(), key=lambda x: (-x[1], x[0]))
        header.append('- 代码语言分布: ' + ', '.join(f"{k}:{v}" for k,v in langs_sorted))
    header.extend(['', '---', ''])
    body: List[str] = []
    msg_counter = 0
    for line in lines:
        msg_counter += 1
        role = '用户' if line['role'] == 'user' else '助手'
        rid = line.get('requestId') or ''
        local_time_full = line.get('localTime') or ''
        utc_time_full = line.get('utcTime') or ''
        # 提取 HH:MM 作为标题中的简短时间
        short_time = ''
        if local_time_full:
            m = re.search(r'\b(\d{2}:\d{2}):\d{2}\b', local_time_full)
            if m:
                short_time = m.group(1)
            else:
                # 如果匹配不到秒，尝试直接找 HH:MM
                m2 = re.search(r'\b(\d{2}:\d{2})\b', local_time_full)
                if m2:
                    short_time = m2.group(1)
        title_extra = f" {short_time}" if short_time else ''
        anchor_title = f"### {msg_counter}. {role}{title_extra}"
        body.append(anchor_title)
        # 引用块中的详细元数据
        meta_lines = []
        if rid:
            meta_lines.append(f"requestId: {rid}")
        if local_time_full:
            meta_lines.append(f"本地时间: {local_time_full}")
        if utc_time_full:
            meta_lines.append(f"UTC: {utc_time_full}")
        if meta_lines:
            body.append('')
            body.append('> ' + '\n> '.join(meta_lines))
        body.append('')
        body.append(line['content'])
        # 文件嵌入段
        if embed_ctx and embed_ctx.get('enable'):
            file_refs = line.get('fileRefs') or []
            injected_parts: List[str] = []
            for fr in file_refs:
                embed_md = try_embed_file(fr, embed_ctx)
                if embed_md:
                    injected_parts.append(embed_md)
            if injected_parts:
                body.append('')
                body.append('#### 附件')
                body.extend(injected_parts)
        body.append('')
    return '\n'.join(header + body).rstrip() + '\n'

# ---------------- 文件嵌入支持 ----------------
IMAGE_EXTS: Set[str] = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg'}
VIDEO_EXTS: Set[str] = {'.mp4', '.mov', '.webm', '.mkv', '.avi'}
TEXT_EXTS: Set[str] = {'.txt', '.log', '.json', '.xml', '.yml', '.yaml', '.md', '.csv'}
CODE_EXTS: Set[str] = {'.cs', '.ts', '.js', '.tsx', '.jsx', '.py', '.java', '.go', '.rs', '.cpp', '.c', '.h', '.sql', '.ps1', '.sh'}

def build_embed_context(args) -> Dict[str, Any]:
    return {
        'enable': True,
        'file_root': os.path.abspath(args.file_root),
        'img_max': args.image_max_bytes,
        'text_max': args.text_max_bytes,
        'assets_dir_name': args.assets_dir_name,
    'verbose': getattr(args, 'embed_verbose', False),
    }

def safe_read_bytes(path: str, max_len: int) -> Optional[bytes]:
    try:
        size = os.path.getsize(path)
        if size > max_len:
            return None
        with open(path, 'rb') as f:
            return f.read()
    except Exception:
        return None

def guess_code_language(ext: str) -> str:
    mapping = {
        '.cs': 'csharp', '.ts': 'typescript', '.js': 'javascript', '.tsx': 'tsx', '.jsx': 'jsx', '.py': 'python',
        '.java': 'java', '.go': 'go', '.rs': 'rust', '.cpp': 'cpp', '.c': 'c', '.h': 'c', '.sql': 'sql',
        '.ps1': 'powershell', '.sh': 'bash', '.json': 'json', '.yml': 'yaml', '.yaml': 'yaml'
    }
    return mapping.get(ext.lower(), '')

def ensure_assets_dir(md_output_path: str, assets_dir_name: str) -> str:
    base_dir = os.path.dirname(os.path.abspath(md_output_path))
    assets_dir = os.path.join(base_dir, assets_dir_name)
    os.makedirs(assets_dir, exist_ok=True)
    return assets_dir

def _normalize_raw_path(raw_path: str) -> str:
    """规范化引用的原始路径:
    - 去除包裹引号
    - 解码 file:// URI
    - 去掉开头多余空白
    - 处理 /d:/ 形式的 Windows 盘符前导斜杠
    - 统一分隔符并 normpath
    """
    if not raw_path:
        return raw_path
    raw = raw_path.strip().strip('"').strip("'")
    raw = _decode_file_uri(raw)
    # 处理 /d:/xxx => d:/xxx
    if re.match(r'^/[A-Za-z]:', raw):
        raw = raw[1:]
    # VS Code 某些变量可能给出 d%3A/ 形式已在 _decode_file_uri 中处理
    raw = raw.replace('\\', '/')
    # 避免出现 // 连续
    while '//' in raw:
        raw = raw.replace('//', '/')
    # 对 Windows 绝对路径保持驱动器大小写原样; normpath 会把正斜杠替换为反斜杠(Windows)，这里先记录
    normed = os.path.normpath(raw)
    return normed

def _simplify_name(name: str) -> str:
    """用于模糊匹配的名称简化: 去除所有空白并小写."""
    return re.sub(r'\s+', '', name).lower()

def try_embed_file(ref, ctx: Dict[str, Any]) -> Optional[str]:
    # ref 可以是 str (旧) 或 {'display':..., 'path':...}
    if isinstance(ref, dict):
        display = ref.get('display') or ref.get('path') or 'file'
        raw_path = ref.get('path') or display
    else:
        display = ref
        raw_path = ref
    root = ctx['file_root']
    norm_raw = _normalize_raw_path(raw_path)
    if ctx.get('verbose') and norm_raw != raw_path:
        print(f"[EMBED] 规范化路径: '{raw_path}' -> '{norm_raw}'")
    if ctx.get('verbose'):
        print(f"[EMBED] 引用解析: display='{display}' raw_path='{norm_raw}' root='{root}'")
    # 绝对路径或相对 file_root
    if os.path.isabs(norm_raw):
        candidate = norm_raw
    else:
        candidate = os.path.normpath(os.path.join(root, norm_raw))
    # 若初步不存在, 尝试再次处理 /d:/ 样式(防御性)
    if not os.path.exists(candidate) and re.match(r'^/[A-Za-z]:', norm_raw):
        alt = norm_raw[1:]
        candidate = alt if os.path.isabs(alt) else os.path.join(root, alt)
    # 不存在则尝试名称搜索 & 模糊匹配
    if not os.path.exists(candidate):
        base_name = os.path.basename(norm_raw)
        simple_target = _simplify_name(base_name)
        if ctx.get('verbose'):
            print(f"[EMBED] 初始路径不存在, 开始遍历搜索文件名: {base_name} (simplified='{simple_target}')")
        found_exact = None
        found_fuzzy = None
        for dirpath, _d, files in os.walk(root):
            if base_name in files:
                found_exact = os.path.join(dirpath, base_name)
                break
            # 模糊: 去空白 & 小写后全等
            for f in files:
                if _simplify_name(f) == simple_target:
                    found_fuzzy = os.path.join(dirpath, f)
                    break
            if found_exact or found_fuzzy:
                break
        if found_exact:
            candidate = found_exact
            if ctx.get('verbose'):
                print(f"[EMBED] 名称搜索匹配(精确): {candidate}")
        elif found_fuzzy:
            candidate = found_fuzzy
            if ctx.get('verbose'):
                print(f"[EMBED] 名称搜索匹配(模糊): {candidate}")
        else:
            if ctx.get('verbose'):
                print(f"[EMBED] 未找到文件: {raw_path}")
            return None
    else:
        if ctx.get('verbose'):
            print(f"[EMBED] 找到文件: {candidate}")
    ext = os.path.splitext(candidate)[1].lower()
    # 判定 md 输出路径(推导): candidate 所在目录上一级
    # 在当前实现中我们无法直接得知单个 md 的输出路径，只能使用 candidate 同级输出相对路径(可能不理想)
    md_output_dummy = candidate  # 用其目录作为参考
    if ext in IMAGE_EXTS:
        data = safe_read_bytes(candidate, ctx['img_max'])
        if not data:
            return f"(图片过大未嵌入: {display})"
        b64 = base64.b64encode(data).decode('ascii')
        mime = 'image/svg+xml' if ext == '.svg' else f"image/{ext.lstrip('.').replace('jpg','jpeg')}"
        if ctx.get('verbose'):
            print(f"[EMBED] 嵌入图片: {candidate} -> base64 (size={len(b64)} chars)")
        return f"![{display}](data:{mime};base64,{b64})"
    if ext in VIDEO_EXTS:
        assets_dir = ensure_assets_dir(md_output_dummy, ctx['assets_dir_name'])
        target_name = os.path.basename(candidate)
        target_path = os.path.join(assets_dir, target_name)
        if not os.path.exists(target_path):
            try:
                shutil.copy2(candidate, target_path)
            except Exception as e:
                return f"(视频复制失败 {display}: {e})"
        rel = os.path.relpath(target_path, os.path.dirname(md_output_dummy)).replace('\\', '/')
        if ctx.get('verbose'):
            print(f"[EMBED] 视频复制: {candidate} -> {target_path}")
        return f"<video src='{rel}' controls style='max-width:100%;height:auto;'>您的浏览器不支持视频标签</video>"
    if ext in TEXT_EXTS or ext in CODE_EXTS:
        data = safe_read_bytes(candidate, ctx['text_max'])
        if not data:
            return f"(文本文件过大未内联: {display})"
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            try:
                text = data.decode('gbk')
            except Exception:
                return f"(无法解码文本: {display})"
        lang = guess_code_language(ext)
        fence = lang if lang else ''
        if ctx.get('verbose'):
            print(f"[EMBED] 内联文本/代码: {candidate} (lang={lang}, bytes={len(data)})")
        return f"```{fence}\n{text.rstrip()}\n```"
    # 其它类型: 复制到 assets 并给出链接
    assets_dir = ensure_assets_dir(md_output_dummy, ctx['assets_dir_name'])
    target_name = os.path.basename(candidate)
    target_path = os.path.join(assets_dir, target_name)
    if not os.path.exists(target_path):
        try:
            shutil.copy2(candidate, target_path)
        except Exception as e:
            return f"(文件复制失败 {display}: {e})"
    rel = os.path.relpath(target_path, os.path.dirname(md_output_dummy)).replace('\\', '/')
    if ctx.get('verbose'):
        print(f"[EMBED] 复制其它类型文件: {candidate} -> {target_path}")
    return f"[附件 {display}]({rel})"

def write_output(path: str, content: str) -> None:
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(content)

def convert_file(input_path: str, output_path: Optional[str] = None, embed_ctx: Optional[Dict[str, Any]] = None) -> Optional[str]:
    try:
        data = load_json(input_path)
    except Exception as e:
        print(f"[WARN] 读取失败 {input_path}: {e}")
        return None
    if not is_chat_session_json(data):
        print(f"[WARN] 跳过 (非聊天会话结构): {input_path}")
        return None
    if not output_path:
        base, _ = os.path.splitext(input_path)
        output_path = base + '.md'
    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    lines = extract_messages(data)
    # 针对嵌入, 需要让 try_embed_file 获知当前 md 输出目录, 复制并相对引用 assets
    if embed_ctx:
        ctx = dict(embed_ctx)
        ctx['current_md_path'] = output_path
    else:
        ctx = None
    md = render_markdown(data, lines, embed_ctx=ctx)
    write_output(output_path, md)
    print(f"[OK] {os.path.basename(input_path)} -> {os.path.basename(output_path)}")
    return output_path


def convert_path(path: str, embed_ctx: Optional[Dict[str, Any]] = None,
                 progress_cb: Optional[Callable[[int, int, str], None]] = None,
                 cancel_flag: Optional[threading.Event] = None,
                 parallel: bool = False, max_workers: Optional[int] = None,
                 output_dir: Optional[str] = None,
                 file_filter: Optional[Callable[[str], bool]] = None) -> List[str]:
    """批量转换路径下 JSON 会话文件。
    progress_cb: (done, total, current_path)
    cancel_flag: threading.Event() 被设置则中断
    parallel: 并行转换
    file_filter: 用于过滤文件的回调函数，返回 True 表示保留文件
    """
    if os.path.isfile(path):
        if cancel_flag and cancel_flag.is_set():
            return []
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            md_name = os.path.splitext(os.path.basename(path))[0] + '.md'
            out_path = os.path.join(output_dir, md_name)
        else:
            out_path = None
        if file_filter and not file_filter(path):
            return []
        res = convert_file(path, out_path, embed_ctx=embed_ctx)
        if progress_cb:
            progress_cb(1, 1, path)
        return [res] if res else []
    # 收集所有 json 文件
    all_json: List[str] = []
    for root, _dirs, files in os.walk(path):
        for fn in files:
            if fn.lower().endswith('.json'):
                full_path = os.path.join(root, fn)
                if not file_filter or file_filter(full_path):
                    all_json.append(full_path)
    total = len(all_json)
    if total == 0:
        print('[INFO] 未找到可转换的 JSON 文件')
        return []
    converted: List[str] = []
    done = 0
    if parallel and total > 1:
        if max_workers is None:
            max_workers = min(8, (os.cpu_count() or 4))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_map = {}
            for p in all_json:
                if output_dir:
                    rel = os.path.relpath(p, path)
                    rel_base, _ = os.path.splitext(rel)
                    out_path = os.path.join(output_dir, rel_base + '.md')
                else:
                    out_path = None
                future_map[ex.submit(convert_file, p, out_path, embed_ctx)] = p
            for fut in as_completed(future_map):
                p = future_map[fut]
                if cancel_flag and cancel_flag.is_set():
                    break
                md = fut.result()
                if md:
                    converted.append(md)
                done += 1
                if progress_cb:
                    progress_cb(done, total, p)
    else:
        for p in all_json:
            if cancel_flag and cancel_flag.is_set():
                break
            if output_dir:
                rel = os.path.relpath(p, path)
                rel_base, _ = os.path.splitext(rel)
                out_path = os.path.join(output_dir, rel_base + '.md')
            else:
                out_path = None
            md = convert_file(p, out_path, embed_ctx=embed_ctx)
            if md:
                converted.append(md)
            done += 1
            if progress_cb:
                progress_cb(done, total, p)
    if cancel_flag and cancel_flag.is_set():
        print(f'[INFO] 已取消, 完成 {len(converted)}/{total}')
    else:
        print(f'[INFO] 完成: {len(converted)} / {total} 个文件')
    return converted

def main():
    parser = argparse.ArgumentParser(
        description='将 VS Code / Copilot Chat 会话 JSON(version=3) 转换为 Markdown; 支持目录递归.')
    parser.add_argument('input', nargs='?', default='.', help='输入文件或目录(缺省=当前目录)')
    parser.add_argument('-o', '--output', help='当 input 为单个文件时指定输出 Markdown 文件; 目录模式忽略')
    parser.add_argument('--embed-files', action='store_true', help='尝试嵌入引用的文件(图片->base64, 视频->复制, 代码->内联)')
    parser.add_argument('--file-root', default='.', help='搜索文件根目录(默认当前工作目录)')
    parser.add_argument('--image-max-bytes', type=int, default=2_000_000, help='单个图片最大嵌入字节数 (默认2MB)')
    parser.add_argument('--text-max-bytes', type=int, default=200_000, help='文本/代码文件最大内联字节(默认200KB)')
    parser.add_argument('--assets-dir-name', default='assets', help='视频/大文件复制目标子目录名 (相对输出md所在目录)')
    parser.add_argument('--embed-verbose', action='store_true', help='嵌入文件时输出详细调试日志')
    args = parser.parse_args()

    path = args.input
    if not os.path.exists(path):
        print(f'[ERROR] 输入路径不存在: {path}')
        return
    embed_ctx = None
    if args.embed_files:
        embed_ctx = build_embed_context(args)
    if os.path.isdir(path):
        print(f'[INFO] 目录模式: 递归转换 {os.path.abspath(path)}')
        convert_path(path, embed_ctx=embed_ctx)
    else:
        out = convert_file(path, args.output, embed_ctx=embed_ctx)
        if out:
            print(f'已生成: {out}')

if __name__ == '__main__':
    main()
