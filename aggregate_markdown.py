#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aggregate_markdown.py
遍历目录(递归)收集所有 Markdown(.md) 文件, 合并生成一个汇总 Markdown。

特性:
  - 默认遍历当前工作目录
  - 支持指定根目录及输出文件名 (-o)
  - 自动跳过聚合输出文件自身 & 以"~"开头的临时文件
  - 生成文件级目录(索引), 每个文件以二级标题开头 `## <相对路径>`
  - 可通过 --sort name|mtime 控制排序 (默认 name)
  - 可通过 --include / --exclude 使用正则(相对路径)过滤
  - UTF-8 读取, 失败时自动跳过并警告
  - 简单 slug 生成保证目录跳转

用法示例:
    python aggregate_markdown.py
    python aggregate_markdown.py path/to/notes -o ALL_NOTES.md
    python aggregate_markdown.py . --sort mtime --exclude "(^|/)archive/"

注意:
  - 若文件第1行已是 # 开头标题, 聚合时会保留, 并在其前插入一个 HTML 注释指示原路径
"""
from __future__ import annotations
import argparse
import os
import re
import sys
from datetime import datetime
from typing import List, Tuple

MD_EXTS = {'.md', '.markdown'}

SlugCache = {}

def make_slug(text: str) -> str:
    base = re.sub(r'[^0-9A-Za-z\u4e00-\u9fa5]+', '-', text.strip()).strip('-').lower()
    if not base:
        base = 'section'
    slug = base
    i = 2
    while slug in SlugCache:
        slug = f"{base}-{i}"
        i += 1
    SlugCache[slug] = True
    return slug

def iter_markdown_files(root: str) -> List[str]:
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 可在此处过滤目录 (如 .git 等)
        dirnames[:] = [d for d in dirnames if d not in {'.git', '.idea', '.svn', '.hg', '__pycache__'}]
        for fn in filenames:
            if fn.startswith('~'):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in MD_EXTS:
                full = os.path.join(dirpath, fn)
                files.append(full)
    return files

def filter_files(files: List[str], root: str, include_pat: str|None, exclude_pat: str|None) -> List[str]:
    def rel(p: str) -> str:
        return os.path.relpath(p, root).replace('\\', '/')
    include_re = re.compile(include_pat) if include_pat else None
    exclude_re = re.compile(exclude_pat) if exclude_pat else None
    out = []
    for f in files:
        r = rel(f)
        if include_re and not include_re.search(r):
            continue
        if exclude_re and exclude_re.search(r):
            continue
        out.append(f)
    return out

def sort_files(files: List[str], mode: str) -> List[str]:
    if mode == 'mtime':
        return sorted(files, key=lambda p: os.path.getmtime(p))
    return sorted(files, key=lambda p: os.path.basename(p).lower())

def read_file(path: str) -> Tuple[str, str]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read(), ''
    except Exception as e:
        return '', str(e)

def build_index(entries: List[Tuple[str, str]]) -> str:
    lines = ['# 汇总索引', '']
    for rel_path, slug in entries:
        lines.append(f"- [{rel_path}](#{slug})")
    lines.append('\n---\n')
    return '\n'.join(lines)

def aggregate(root: str, output: str, sort_mode: str, include_pat: str|None, exclude_pat: str|None) -> str:
    root = os.path.abspath(root)
    all_files = iter_markdown_files(root)
    all_files = filter_files(all_files, root, include_pat, exclude_pat)
    output_abs = os.path.abspath(output)
    all_files = [f for f in all_files if os.path.abspath(f) != output_abs]
    if not all_files:
        raise SystemExit('未发现可聚合的 Markdown 文件')
    all_files = sort_files(all_files, sort_mode)

    rel_slug_pairs: List[Tuple[str, str]] = []  # (显示名称, slug)
    sections: List[str] = []

    guid_re = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
    date_in_localtime_re = re.compile(r'^> \s*本地时间: (\d{4}-\d{2}-\d{2})', re.MULTILINE)
    heading_re = re.compile(r'^#\s*会话记录\s*(.+)$', re.MULTILINE)

    def derive_display_name(rel_path: str, content: str) -> str:
        fname = os.path.splitext(os.path.basename(rel_path))[0]
        if guid_re.match(fname) or (len(fname) > 24 and re.fullmatch(r'[0-9a-fA-F-]+', fname)):
            # 尝试提取日期
            m_date = date_in_localtime_re.search(content)
            date_part = m_date.group(1) if m_date else ''
            m_head = heading_re.search(content)
            head_tail = m_head.group(1).strip() if m_head else ''
            # 如果标题尾部仍是 GUID, 剪裁为前8位
            if guid_re.match(head_tail):
                head_tail = head_tail[:8]
            if date_part and head_tail:
                return f"会话记录 {date_part} {head_tail}"
            if date_part:
                return f"会话记录 {date_part}"
            if head_tail:
                return f"会话记录 {head_tail}"
            # 回退为文件相对路径
            return rel_path
        return rel_path

    for path in all_files:
        rel_path = os.path.relpath(path, root).replace('\\', '/')
        content, err = read_file(path)
        if err:
            print(f"[WARN] 读取失败 {rel_path}: {err}")
            continue
        slug = make_slug(rel_path)
        display_name = derive_display_name(rel_path, content)
        rel_slug_pairs.append((display_name, slug))
        header = f"## {rel_path}\n"  # 二级标题
        # 如果文件首行已有#标题, 保留, 但前面插入注释说明来源
        decorated = f"<!-- SOURCE: {rel_path} -->\n" + content.strip() + '\n'
        sections.append(f"<a id='{slug}'></a>\n{header}\n{decorated}\n")

    index_md = build_index(rel_slug_pairs)
    meta = f"<!-- Generated at {datetime.now().isoformat()} from root {root} -->\n\n"
    final_md = meta + index_md + '\n'.join(sections)
    with open(output, 'w', encoding='utf-8', newline='') as fw:
        fw.write(final_md)
    return output

def main():
    parser = argparse.ArgumentParser(description='聚合目录内全部 Markdown 文件为一个汇总文档')
    parser.add_argument('path', nargs='?', default='.', help='根目录 (默认当前目录)')
    parser.add_argument('-o', '--output', default='AGGREGATED.md', help='输出文件名 (默认 AGGREGATED.md)')
    parser.add_argument('--sort', choices=['name', 'mtime'], default='name', help='排序方式')
    parser.add_argument('--include', help='仅包含匹配该正则的相对路径')
    parser.add_argument('--exclude', help='排除匹配该正则的相对路径')
    args = parser.parse_args()

    out = aggregate(args.path, args.output, args.sort, args.include, args.exclude)
    print(f'已生成: {out}')

if __name__ == '__main__':
    main()
