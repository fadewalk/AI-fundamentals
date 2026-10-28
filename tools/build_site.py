#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组装 mkdocs 站点：上游 markdown 零改动，只做组织方式增强。

用法：python3 tools/build_site.py
产物：build/mkdocs.yml + build/docs/（随后 `mkdocs build -f build/mkdocs.yml`）

规则：
- 复制仓库全部内容到 build/docs/，排除：隐藏目录、工具目录、以及
  .pptx/.ppt/.mp4/.zip 演示文稿（这些 md 链接改写为上游 blob 地址，点开去 GitHub 看/下载）
- 每个目录的 README.md 作为章节首页（section-index），导航树按目录结构自动生成
- 标题取每个 md 的第一个 H1，回退文件名
"""
import json
import pathlib
import posixpath
import re
import shutil
import sys
import urllib.parse
import urllib.request

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DOCS = BUILD / "docs"
UPSTREAM = "https://github.com/ForceInjection/AI-fundamentals"
SITE_URL = "https://fadewalk.github.io/AI-fundamentals/"

SKIP_DIRS = {".git", ".github", ".trae", ".qoder", ".claude", ".venv", "node_modules",
             "__pycache__", "build", "tools", "site"}
LINKOUT_EXT = {".pptx", ".ppt", ".mp4", ".zip"}  # 不进站点，链接指回上游
SKIP_FILE = re.compile(r"^\.|\.pyc$|\.DS_Store")
SKIP_TOP_FILES = {"README.md"}  # 根 README 由生成的 index.md（落地页）替代


def first_h1(path: pathlib.Path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f.read().splitlines()[:120]:
                m = re.match(r"^#\s+(.+?)\s*$", line)
                if m:
                    t = re.sub(r"[*`]", "", m.group(1))
                    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
                    return t.strip()[:60] or None
    except OSError:
        pass
    return None


def blob_url(repo_path: str) -> str:
    return UPSTREAM + "/blob/main/" + urllib.parse.quote(repo_path)


def copy_tree():
    n_md = n_out = 0
    for src in ROOT.rglob("*"):
        rel = src.relative_to(ROOT)
        if src.is_dir():
            if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
                continue
            continue
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        name = rel.parts[-1]
        if SKIP_FILE.match(name):
            continue
        if len(rel.parts) == 1 and name in SKIP_TOP_FILES:
            continue
        if src.suffix.lower() in LINKOUT_EXT:
            n_out += 1
            continue
        dst = DOCS / rel
        if name == "README.md" and len(rel.parts) > 1 and any((src.parent / x).exists() for x in ("index.html", "index.md")):
            dst = dst.with_name("readme-doc.md")  # 与目录自带 index 冲突，改名保留（同目录，相对链接不变）
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if src.suffix.lower() == ".md":
            n_md += 1
    return n_md, n_out


LINK_MD = re.compile(r"(\]\()([^)\s]+)([^)]*\))")
LINK_A = re.compile(r'(<a\s[^>]*href=")([^"]+)(")')


def resolve(base_rel: str, target: str) -> str | None:
    """把 md 里的相对/仓库内链接解析成仓库相对路径；外链返回 None。"""
    if re.match(r"^[a-z]+://|^mailto:|^#", target, re.I):
        return None
    t = target.split("#")[0].strip()
    if not t:
        return None
    if t.startswith("/"):
        return posixpath.normpath(t.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_rel), t))


def rewrite_links():
    n = 0
    for md in DOCS.rglob("*.md"):
        rel = md.relative_to(DOCS).as_posix()
        text = md.read_text(encoding="utf-8", errors="ignore")

        def retarget(p):
            """返回改写后的链接目标（相对当前 md），None 表示不改。"""
            if not p or not (ROOT / p).exists():
                return None
            if pathlib.PurePosixPath(p).suffix.lower() in LINKOUT_EXT:
                return blob_url(p)
            if p == "README.md":
                return posixpath.relpath("index.md", posixpath.dirname(rel) or ".")
            pp = pathlib.PurePosixPath(p)
            if pp.name == "README.md" and any((ROOT / pp.parent / x).exists() for x in ("index.html", "index.md")):
                return posixpath.relpath(str(pp.with_name("readme-doc.md")), posixpath.dirname(rel) or ".")
            return None

        def sub_md(m):
            nonlocal n
            frag = m.group(2).split("#", 1)
            t = retarget(resolve(rel, m.group(2)))
            if t:
                n += 1
                return m.group(1) + t + ("#" + frag[1] if len(frag) > 1 else "") + m.group(3)
            return m.group(0)

        def sub_a(m):
            nonlocal n
            frag = m.group(2).split("#", 1)
            t = retarget(resolve(rel, m.group(2)))
            if t:
                n += 1
                return m.group(1) + t + ("#" + frag[1] if len(frag) > 1 else "") + m.group(3)
            return m.group(0)

        new = LINK_MD.sub(sub_md, text)
        new = LINK_A.sub(sub_a, new)
        if new != text:
            md.write_text(new, encoding="utf-8")
    return n


def dir_title(d: pathlib.Path) -> str:
    rd = d.relative_to(ROOT)
    readme = d / "README.md"
    if readme.exists():
        h = first_h1(readme)
        if h and h.lower() != "readme":
            return h
    name = rd.as_posix()
    m = re.match(r"^(\d+)[_-]?(.+)$", name)
    pretty = (m.group(2) if m else name).replace("_", " ").strip()
    return pretty or name


def file_title(md: pathlib.Path) -> str:
    h = first_h1(md)
    if h:
        return h
    return md.stem


def sort_key(p: pathlib.Path):
    name = p.name
    m = re.match(r"^(\d+)", name)
    return (0, int(m.group(1)), name.lower()) if m else (1, 0, name.lower())


def nav_tree(base: pathlib.Path, rel_prefix=""):
    """返回该目录的 nav 列表（dict/list 混合，json 序列化进 mkdocs.yml）。"""
    items = []
    entries = sorted(
        [p for p in base.iterdir()
         if not p.name.startswith(".")
         and p.name not in SKIP_DIRS
         and (p.is_dir() or p.suffix.lower() == ".md")],
        key=sort_key,
    )
    for p in entries:
        rel = (rel_prefix + p.name) if rel_prefix else p.name
        if p.is_dir():
            sub = nav_tree(p, rel + "/")
            if sub:
                items.append({dir_title(p): sub})
        elif p.name == "README.md":
            continue  # 由父层作为章节首页插入
        else:
            items.append({file_title(p): rel})
    # 目录自身的 README 作为章节首页，插在最前（section-index 渲染为可点的章节链接）
    readme = base / "README.md"
    if readme.exists():
        has_own_index = any((base / x).exists() for x in ("index.html", "index.md"))
        target = rel_prefix + ("readme-doc.md" if has_own_index else "README.md")
        items.insert(0, {dir_title(base) + " · 章节导读": target})
    return items


SECTION_INTRO = {}

INDEX_MD = """# AI-Fundamentals · 中文 AI 基础设施知识库（导航增强版）

本站是开源仓库 [{repo}]({repo}) 的**只读镜像**：**全部文档内容一字未改**，
只重新做了组织方式——左侧目录树、右上角全文搜索、每页底部上一页/下一页、深浅色切换。

!!! tip "为什么有这个站"
    原仓库 590+ 篇 Markdown 只有平铺的 README 链接树，没有菜单、没有搜索、来回跳转要反复"后退"。
    本站用 mkdocs-material 按目录结构自动生成导航，每周自动同步上游更新。

## 从这里开始

| 章节 | 内容 |
|---|---|
{cards}

## 使用说明

- **左侧目录树**按仓库目录组织，点章节名展开；当前页会自动高亮。
- **右上角搜索**支持中文（试试搜"KV Cache"或"显存"）。
- 每页底部有**上一页 / 下一页**，按目录顺序通读不用来回后退。
- PPT / 视频等大文件不放在本站，点击会跳到 GitHub 原文件页（可在线看或下载）。

## 相关站点

- [AI Infra 可视化学习课程](https://fadewalk.github.io/ai-infra-visual-course/) —— 面向 SRE 转型的小白化可视化课程（动画/交互/面试题库），与本知识库互补：先在课程里建立直觉，再回这里深入。

## 本站如何维护

fork 自上游，`tools/build_site.py` 在构建期生成导航（源文件零改动）；GitHub Actions 每周自动同步上游并重新部署。
""".rstrip()


def gen_index(cards: str):
    (DOCS / "index.md").write_text(INDEX_MD.format(repo=UPSTREAM, cards=cards), encoding="utf-8")


def vendor_js():
    js_dir = DOCS / "javascripts"
    js_dir.mkdir(exist_ok=True)
    (js_dir / "mathjax.js").write_text(
        "window.MathJax = {tex: {inlineMath: [['\\\\(', '\\\\)']], displayMath: [['\\\\[', '\\\\]']]}, "
        "options: {skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']}};\n",
        encoding="utf-8",
    )
    vendored = ["javascripts/mathjax.js"]  # 配置必须先于 mathjax 主库加载
    for name, url in [
        ("tex-mml-chtml.js", "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"),
    ]:
        dst = js_dir / name
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as f:
                shutil.copyfileobj(r, f)
            vendored.append("javascripts/" + name)
        except Exception as e:  # noqa: BLE001 - 回退到 CDN 直链
            print(f"  [warn] vendor {name} 失败，回退 CDN 直链: {e}")
            vendored.append(url)
    return vendored


CFG_HEAD = """site_name: AI-Fundamentals · AI 基础设施知识库
site_url: {site_url}
site_description: 中文 AI 基础设施知识库（硬件/集群/云原生/训练/推理/Agent/RAG）——导航增强镜像
docs_dir: docs
use_directory_urls: true
repo_url: {repo}
repo_name: ForceInjection/AI-fundamentals
edit_uri: blob/main/
theme:
  name: material
  language: zh
  features:
    - navigation.tracking
    - navigation.footer
    - navigation.top
    - navigation.path
    - search.highlight
    - search.share
    - content.code.copy
  palette:
    - scheme: default
      primary: indigo
      toggle: {{icon: material/brightness-7, name: 切换到暗色}}
    - scheme: slate
      primary: indigo
      toggle: {{icon: material/brightness-4, name: 切换到亮色}}
plugins:
  - search:
      separator: '[\\s\\-\\.,:;!?()\\[\\]{{}}"/=|]+|([A-Za-z0-9]+)|(\\p{{Han}}+)'
  - section-index
markdown_extensions:
  - admonition
  - toc:
      permalink: true
  - pymdownx.arithmatex:
      generic: true
  - pymdownx.superfences:
      custom_fences:
        - name: mermaid
          class: mermaid
          format: !!python/name:pymdownx.superfences.fence_code_format
"""


def main():
    if BUILD.exists():
        shutil.rmtree(BUILD)
    DOCS.mkdir(parents=True)

    print("[1/5] 复制内容…")
    n_md, n_out = copy_tree()
    print(f"      {n_md} 篇 md，{n_out} 个演示文稿改为链接回 GitHub")

    print("[2/5] 改写指向大文件的链接…")
    n_rw = rewrite_links()
    print(f"      改写 {n_rw} 处链接")

    print("[3/5] 生成导航树…")
    nav = [{"首页": "index.md"}]
    cards = []
    top_dirs = sorted([p for p in ROOT.iterdir() if p.is_dir() and p.name[0].isdigit()], key=sort_key)
    for d in top_dirs:
        sub = nav_tree(d, d.name + "/")
        if sub:
            title = dir_title(d)
            nav.append({title: sub})
            n = len([x for x in d.rglob("*.md")])
            cards.append(f"| [{title}](./{d.name}/) | {n} 篇 |")
    nav.append({"仓库信息": [
        {"演示与实验索引": "demos.md"},
        {"AGENTS.md": "AGENTS.md"},
        {"CLAUDE.md（写作与提交规范）": "CLAUDE.md"},
    ]})
    gen_index("\n".join(cards))

    print("[4/5] 下载前端依赖（mermaid/mathjax，失败则回退 CDN）…")
    vendored = vendor_js()

    print("[5/5] 写 mkdocs.yml…")
    cfg = CFG_HEAD.format(site_url=SITE_URL, repo=UPSTREAM)
    cfg += "extra_javascript:\n" + "".join(f"  - {u}\n" for u in vendored)
    nav_yaml = yaml.safe_dump(nav, allow_unicode=True, sort_keys=False, width=120, default_flow_style=False)
    cfg += "nav:\n" + nav_yaml
    (BUILD / "mkdocs.yml").write_text(cfg, encoding="utf-8")
    print(f"完成：{len(nav)} 个一级章节 → build/mkdocs.yml；接下来 mkdocs build -f build/mkdocs.yml")


if __name__ == "__main__":
    sys.exit(main())
