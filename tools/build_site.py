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
    css_dir = DOCS / "css"
    css_dir.mkdir(parents=True, exist_ok=True)
    (css_dir / "custom.css").write_text(open(pathlib.Path(__file__).with_name("custom.css"), encoding="utf-8").read(), encoding="utf-8")
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
    """目录标题：优先 README 的 H1，带目录的数字前缀作章节序号（01 · xxx）。"""
    name = d.name  # 只看自身目录名——嵌套子目录不该继承顶级章节的序号
    m = re.match(r"^(\d+)[_-]?(.+)$", name)
    num = m.group(1) if m else None
    pretty = (m.group(2) if m else name).replace("_", " ").strip()
    readme = d / "README.md"
    if readme.exists():
        h = first_h1(readme)
        if h and h.lower() != "readme":
            pretty = h
    title = pretty or name
    return f"{num} · {title}" if num else title


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
        items.insert(0, {"章节导读": target})
    return items


CH_META = {
  "01_hardware_architecture": ("material-memory", "GPU 架构 / NVLink / PCIe / GPUDirect / 延迟金字塔"),
  "02_dpu_programming": ("material-server-network", "DOCA / DPU 卸载编程"),
  "02_gpu_programming": ("material-chip", "CUDA 范式 / Kernel 调优 / Profiling"),
  "02_npu_programming": ("material-developer-board", "昇腾 Ascend C / NPU 生态"),
  "03_ai_cluster_ops": ("material-dns", "GPU 运维 / IB 网络 / NCCL / XID 排障"),
  "04_cloud_native_ai_platform": ("material-cloud", "K8s GPU 调度 / HAMi / Kueue / 存储"),
  "05_model_training_and_fine_tuning": ("material-fire", "训练循环 / 并行策略 / SFT / 对齐 / 框架"),
  "06_llm_theory_and_fundamentals": ("material-lightbulb-outline", "Token / Transformer / MoE / 量化"),
  "07_rag_and_tools": ("material-text-search", "RAG / GraphRAG / 分块 / PDF 解析"),
  "08_agentic_system": ("material-robot", "ReAct / MCP / 记忆 / 多 Agent"),
  "09_inference_system": ("material-rocket-launch", "KV Cache / vLLM / SGLang / PD 分离 / 投机解码"),
  "10_ai_related_course": ("material-school", "讲座 / 外部教程地图 / AI 编译器"),
  "11_ai_native_everything": ("material-briefcase", "AI Native 方法论 / FDE 实践"),
  "98_llm_programming": ("material-code-braces", "LangGraph / Spring AI / Harness"),
  "99_misc": ("material-bookshelf", "上手机会 / 任务卡 / 书单"),
}

SECTION_INTRO = {}

INDEX_MD = """# AI-Fundamentals · 中文 AI 基础设施知识库（导航增强版）

本站是开源仓库 [{repo}]({repo}) 的**只读镜像**：**全部文档内容一字未改**，
只重新做了组织方式——左侧目录树、右上角全文搜索、每页底部上一页/下一页、深浅色切换。

!!! tip "为什么有这个站"
    原仓库 590+ 篇 Markdown 只有平铺的 README 链接树，没有菜单、没有搜索、来回跳转要反复"后退"。
    本站用 mkdocs-material 按目录结构自动生成导航，每周自动同步上游更新。

## 章节导航

<div class="grid cards" markdown>

{cards}

</div>

!!! tip "上手顺序建议"
    按 `01 硬件 → 03 集群运维 → 04 云原生 → 05 训练 → 09 推理` 的顺序读，正好是一条 **AI Infra SRE 的成长路径**；98/99 章适合动手阶段再翻。

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
        "svg: {fontCache: 'global'}, "
        "options: {skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']}};\n"
        "if (window.document$) { window.document$.subscribe(function () { "
        "if (window.MathJax && window.MathJax.typesetPromise) window.MathJax.typesetPromise(); }); }\n",
        encoding="utf-8",
    )
    (js_dir / "mermaid-init.js").write_text(MERMAID_INIT, encoding="utf-8")
    (js_dir / "fonts.js").write_text(FONTS_JS, encoding="utf-8")
    vendored = ["javascripts/mathjax.js"]  # 配置必须先于 mathjax 主库加载
    for name, url in [
        ("tex-svg.js", "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"),  # SVG 输出：字形内嵌，无需 woff 字体
        ("mermaid.min.js", "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"),
        ("mermaid-init.js", None),
        ("fonts.js", None),
    ]:
        dst = js_dir / name
        try:
            if url is None:
                vendored.append("javascripts/" + name)
                continue
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as f:
                shutil.copyfileobj(r, f)
            vendored.append("javascripts/" + name)
        except Exception as e:  # noqa: BLE001 - 回退到 CDN 直链
            print(f"  [warn] vendor {name} 失败，回退 CDN 直链: {e}")
            vendored.append(url)
    # mermaid 库本地化后，material 检测到 window.mermaid 已定义即跳过 unpkg 懒加载，
    # 直接用本地库初始化并渲染（主题自带的初始化含深浅色适配）
    return vendored


MERMAID_INIT = """(function () {
  'use strict';
  function scheme() {
    return (document.body.getAttribute('data-md-color-scheme') === 'slate') ? 'dark' : 'neutral';
  }
  var booted = false;
  function renderAll() {
    if (typeof mermaid === 'undefined') return;
    if (!booted) {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'loose', theme: scheme() });
      booted = true;
    }
    var els = document.querySelectorAll('pre.mermaid-src, div.mermaid-src');
    Array.prototype.forEach.call(els, function (el, i) {
      if (el.dataset.mermaidDone) return;
      el.dataset.mermaidDone = '1';
      var src = el.textContent.replace(/^\\n/, '');
      var holder = document.createElement('div');
      holder.className = 'mermaid-render';
      el.parentNode.replaceChild(holder, el);
      var id = 'mmd-' + Date.now() + '-' + i;
      mermaid.render(id, src).then(function (r) {
        holder.innerHTML = r.svg;
      }).catch(function () {
        holder.className = '';
        var pre = document.createElement('pre');
        pre.className = 'mermaid-fallback';
        pre.textContent = src;
        holder.replaceWith(pre);
      });
    });
  }
  function boot() {
    var src = null;
    for (var i = 0, ss = document.scripts; i < ss.length; i++)
      if (ss[i].src && /mermaid\.min\.js/.test(ss[i].src)) { src = ss[i].src; break; }
    if (!src) return;
    var s = document.createElement('script');
    s.src = src; // 同一 URL 命中浏览器缓存，不产生第二次下载
    s.onload = renderAll;
    document.head.appendChild(s);
    if (window.document$) window.document$.subscribe(renderAll);
    else document.addEventListener('DOMContentLoaded', renderAll);
  }
  boot();
})();
"""

FONTS_JS = """(function () {
  'use strict';
  var FKEY = 'aivc_docs_font', SKEY = 'aivc_docs_size';
  var MIN = 70, MAX = 150, STEP = 5, BASE = 0.8; /* 正文基准 .8rem，按百分比我缩放 */
  var FONTS = [
    { id: 'default', name: '字体：默认黑体' },
    { id: 'song', name: '字体：宋体' },
    { id: 'kai', name: '字体：楷体' },
    { id: 'fang', name: '字体：仿宋' },
    { id: 'yahei', name: '字体：雅黑等宽' }
  ];
  var STACK = {
    song: '"Songti SC","STSong","SimSun","Noto Serif SC",serif',
    kai: '"Kaiti SC","STKaiti","KaiTi","Noto Serif SC",serif',
    fang: '"Fangti SC","STFangsong","FangSong","Noto Serif SC",serif',
    yahei: '"Microsoft YaHei Mono","Sarasa Mono SC","Microsoft YaHei","PingFang SC",monospace'
  };
  function get(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function fontId() { var id = get(FKEY, 'default'); return STACK[id] ? id : 'default'; }
  function sizePct() { var n = parseInt(get(SKEY, '100'), 10); if (isNaN(n) || n < MIN || n > MAX) n = 100; return n; }
  function apply() {
    var f = fontId(), p = sizePct();
    document.documentElement.setAttribute('data-docfont', f);
    document.documentElement.setAttribute('data-docsize', String(p));
    var fs = document.getElementById('docfont-select'); if (fs) fs.value = f;
    var v = document.getElementById('docsize-val'); if (v) v.textContent = p + '%';
    var mi = document.getElementById('docsize-minus'), pl = document.getElementById('docsize-plus');
    if (mi) mi.style.opacity = p <= MIN ? .35 : 1;
    if (pl) pl.style.opacity = p >= MAX ? .35 : 1;
  }
  function bump(d) {
    var p = Math.min(MAX, Math.max(MIN, sizePct() + d));
    set(SKEY, String(p)); apply();
  }
  function inject() {
    var css = document.createElement('style');
    var fontRules = Object.keys(STACK).map(function (id) {
      return 'html[data-docfont="' + id + '"]{--md-text-font:' + STACK[id] + '!important;--md-text-font-family:' + STACK[id] + '!important}';
    }).join('');
    var sizeRules = '';
    for (var p = MIN; p <= MAX; p += STEP) {
      sizeRules += 'html[data-docsize="' + p + '"] .md-typeset{font-size:' + (BASE * p / 100).toFixed(3) + 'rem}';
    }
    css.textContent = fontRules + sizeRules
      + '.docfont-wrap{display:inline-flex;align-items:center;margin:0 8px 0 2px;flex-shrink:0}'
      + '.docfont-wrap select{appearance:none;-webkit-appearance:none;-moz-appearance:none;border:0;outline:0;color:inherit;'
      + 'background:rgba(255,255,255,.14) url("data:image/svg+xml;charset=utf-8,' + "%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 24 24%22%3E%3Cpath fill=%22%23fff%22 d=%22M7 10l5 5 5-5z%22/%3E%3C/svg%3E" + '") no-repeat right 5px center;'
      + 'border-radius:7px;font-size:12.5px;line-height:1;padding:6px 24px 6px 10px;cursor:pointer;max-width:130px;height:28px}'
      + '.docfont-wrap select option{color:#1a1d23;background:#fff}'
      + '#docsize-wrap{gap:0;background:rgba(255,255,255,.14);border-radius:7px;height:28px}'
      + '#docsize-wrap button{border:0;background:transparent;color:inherit;cursor:pointer;font-size:15px;line-height:1;width:26px;height:100%;padding:0;opacity:.9}'
      + '#docsize-wrap button:hover{opacity:1}'
      + '#docsize-val{font-size:12px;line-height:1;min-width:38px;text-align:center}'
      + '@media screen and (max-width:44.98em){.docfont-wrap{margin:0 6px 0 0}.docfont-wrap select{font-size:11.5px;padding:5px 20px 5px 8px;max-width:104px;height:26px}#docsize-wrap{height:26px;margin:0 6px 0 0}#docsize-val{min-width:32px;font-size:11px}}';
    document.head.appendChild(css);
    var opts = document.querySelectorAll('.md-header__option');
    var anchor = opts.length ? opts[opts.length - 1] : document.querySelector('.md-header__inner');
    if (!anchor) return;
    var w1 = document.createElement('span'); w1.className = 'docfont-wrap';
    var sel = document.createElement('select');
    sel.id = 'docfont-select'; sel.setAttribute('aria-label', '正文字体');
    FONTS.forEach(function (m) {
      var o = document.createElement('option');
      o.value = m.id; o.textContent = m.name;
      sel.appendChild(o);
    });
    sel.value = fontId();
    sel.addEventListener('change', function () { set(FKEY, sel.value); apply(); });
    w1.appendChild(sel);
    var w2 = document.createElement('span'); w2.className = 'docfont-wrap'; w2.id = 'docsize-wrap';
    var mi = document.createElement('button'); mi.id = 'docsize-minus'; mi.type = 'button'; mi.textContent = '−'; mi.setAttribute('aria-label', '缩小字号');
    var val = document.createElement('span'); val.id = 'docsize-val';
    var pl = document.createElement('button'); pl.id = 'docsize-plus'; pl.type = 'button'; pl.textContent = '+'; pl.setAttribute('aria-label', '放大字号');
    mi.addEventListener('click', function () { bump(-STEP); });
    pl.addEventListener('click', function () { bump(STEP); });
    w2.appendChild(mi); w2.appendChild(val); w2.appendChild(pl);
    anchor.appendChild(w1);
    anchor.appendChild(w2);
  }
  if (window.document$) { window.document$.subscribe(apply); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { inject(); apply(); });
  } else {
    inject();
    apply();
  }
})();
"""

CFG_HEAD = """site_name: AI-Fundamentals · AI 基础设施知识库
site_url: {site_url}
site_description: 中文 AI 基础设施知识库（硬件/集群/云原生/训练/推理/Agent/RAG）——导航增强镜像
docs_dir: docs
use_directory_urls: true
theme:
  name: material
  language: zh
  features:
    - navigation.tracking
    - navigation.footer
    - navigation.top
    - navigation.path
    - toc.follow
    - search.suggest
    - search.highlight
    - search.share
    - content.code.copy
  icon:
    logo: material/book-open-page-variant
  palette:
    - scheme: default
      primary: indigo
      toggle: {{icon: material/brightness-7, name: 切换到暗色}}
    - scheme: slate
      primary: indigo
      toggle: {{icon: material/brightness-4, name: 切换到亮色}}
extra_css:
  - css/custom.css
plugins:
  - search:
      lang: ja
  - section-index
markdown_extensions:
  - admonition
  - attr_list
  - md_in_html
  - pymdownx.details
  - pymdownx.emoji:
      emoji_index: !!python/name:material.extensions.emoji.twemoji
      emoji_generator: !!python/name:material.extensions.emoji.to_svg
  - toc:
      permalink: true
  - pymdownx.arithmatex:
      generic: true
  - pymdownx.superfences:
      custom_fences:
        - name: mermaid
          class: mermaid-src
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
            icon, desc = CH_META.get(d.name, ("material-book-open-variant", ""))
            cards.append(f"- :{icon}: **[{title}]({d.name}/)**\n\n    ---\n\n    {n} 篇 · {desc}")
    nav.append({"仓库信息": [
        {"演示与实验索引": "demos.md"},
        {"AGENTS.md": "AGENTS.md"},
        {"CLAUDE.md（写作与提交规范）": "CLAUDE.md"},
    ]})
    gen_index("\n\n".join(cards))

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
