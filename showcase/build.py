"""Build the single-page, dependency-free engineering handbook."""
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent
CHAPTERS = [
    '00-navigation.html',
    '01-introduction.html',
    '02-manual.html',
    '03-implementation.html',
    '04-evaluation.html',
    '05-appendix.html',
]
PAGES = [('', 'FineVision 项目展示文档与操作手册')]


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(fragment):
    parser = PlainText()
    parser.feed(fragment)
    return ''.join(parser.parts).strip()


def expand_includes(body):
    """Include reviewed fragments and keep them below the handbook hierarchy."""
    pattern = re.compile(r'<!--\s*include:([^>]+?)\s*-->')

    def include(match):
        relative = Path(match.group(1).strip())
        source = (ROOT / relative).resolve()
        if not source.is_relative_to(ROOT) or not source.is_file():
            raise ValueError(f'Invalid include: {relative}')
        fragment = source.read_text()
        fragment = re.sub(r'<h2([^>]*)>', r'<h4\1>', fragment)
        fragment = fragment.replace('</h2>', '</h4>')
        fragment = re.sub(r'<h3([^>]*)>', r'<h5\1>', fragment)
        return fragment.replace('</h3>', '</h5>')

    return pattern.sub(include, body)


def add_anchors_and_navigation(body):
    headings = []
    used = set()

    def heading(match):
        level, attrs, fragment = match.groups()
        label = plain_text(fragment)
        number = re.match(r'\s*(\d+(?:\.\d+)*)', label)
        base = 'chapter-' + (number.group(1).replace('.', '-') if number else str(len(headings) + 1))
        anchor = base
        suffix = 2
        while anchor in used:
            anchor = f'{base}-{suffix}'
            suffix += 1
        used.add(anchor)
        headings.append((int(level), anchor, label))
        attrs = re.sub(r'\s+id=("[^"]*"|\'[^\']*\')', '', attrs)
        return (
            f'<h{level}{attrs} id="{anchor}">{fragment}'
            f'<a class="anchor" href="#{anchor}" aria-label="链接到{escape(label, quote=True)}">#</a>'
            f'</h{level}>'
        )

    body = re.sub(r'<h([23])([^>]*)>(.*?)</h\1>', heading, body, flags=re.S)
    groups = []
    current = None
    for level, anchor, label in headings:
        if level == 2:
            current = {'anchor': anchor, 'label': label, 'children': []}
            groups.append(current)
        elif current:
            current['children'].append((anchor, label))
    navigation = []
    for group in groups:
        children = ''.join(
            f'<li><a href="#{anchor}">{escape(label)}</a></li>'
            for anchor, label in group['children']
        )
        navigation.append(
            '<li class="toc-chapter">'
            f'<a href="#{group["anchor"]}">{escape(group["label"])}</a>'
            f'<ol>{children}</ol></li>'
        )
    return body, '<ol class="toc-tree">' + ''.join(navigation) + '</ol>'


def build():
    output = ROOT / 'site'
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    shutil.copyfile(ROOT / 'style.css', output / 'style.css')
    shutil.copyfile(ROOT / 'technical.css', output / 'technical.css')
    if (ROOT / 'assets').exists():
        shutil.copytree(ROOT / 'assets', output / 'assets')

    source = '\n'.join((ROOT / 'handbook' / chapter).read_text() for chapter in CHAPTERS)
    body, navigation = add_anchors_and_navigation(expand_includes(source))
    page = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="FineVision 细粒度视觉平台：产品说明、操作手册、实现细节、评估设计与工程附录。">
<title>FineVision 项目展示文档与操作手册</title><link rel="stylesheet" href="./style.css"><link rel="stylesheet" href="./technical.css"></head>
<body><a class="skip" href="#main">跳到正文</a>
<header><a class="brand" href="#top"><span class="logo">F</span> FineVision <small>项目展示文档与操作手册</small></a><div class="header-actions"><a href="http://localhost:5173/" target="_blank" rel="noopener noreferrer">打开本地平台 ↗</a><a class="github" href="https://github.com/tianyuqic1/FGVCplateformPublic" target="_blank" rel="noopener noreferrer">GitHub ↗</a></div></header>
<div class="layout" id="top"><aside><div class="navlabel">FineVision</div><nav aria-label="文档目录">{navigation}</nav><div class="aside-note">CURRENT IMPLEMENTATION · 2026.09<br>产品 / 算法 / 契约 / 状态机 / 取舍<br>48 / 48 测评单元已完成</div></aside>
<main id="main"><div class="hero"><div class="eyebrow">PROJECT DOCUMENTATION · ENGINEERING HANDBOOK</div><h1>FineVision 项目展示文档与操作手册</h1><p class="lead">面向细粒度视觉分类的可审计工程平台：从不可变数据集版本、差异化训练与完整模型发布，到选择性推理、人工反馈和检索增强的 AI 标注闭环。</p><div class="tags"><span>Go Control Plane</span><span>Python Compute</span><span>PostgreSQL + Outbox</span><span>RabbitMQ</span><span>MinIO</span><span>ONNX / TensorRT / Ascend</span><span>Human in the Loop</span></div></div>{body}<footer>FineVision · 本文档描述当前仓库实现与已验证边界。静态展示页不连接业务 API，不触发训练、推理或付费模型调用。</footer></main></div></body></html>'''
    (output / 'index.html').write_text(page)
    print(f'Built single-page handbook → {output}')


if __name__ == '__main__':
    build()
