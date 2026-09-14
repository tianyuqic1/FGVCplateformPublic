"""Check the generated single-page handbook, local assets and anchor integrity."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, unquote
import re

SITE = Path(__file__).resolve().parent / 'site'


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()
        self.h1 = 0
        self.h2 = 0
        self.h3 = 0
        self.main = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            assert attrs['id'] not in self.ids, f'Duplicate HTML id: {attrs["id"]}'
            self.ids.add(attrs['id'])
        for name in ('href', 'src'):
            if name in attrs:
                self.links.append(attrs[name])
        self.h1 += tag == 'h1'
        self.h2 += tag == 'h2'
        self.h3 += tag == 'h3'
        self.main += tag == 'main'


html_files = list(SITE.rglob('*.html'))
assert html_files == [SITE / 'index.html'], f'Expected one handbook page, found: {html_files}'
content = html_files[0].read_text()
page = Page()
page.feed(content)
assert page.h1 == 1 and page.main == 1
assert page.h2 == 6, f'Expected six top-level chapters, found {page.h2}'
assert page.h3 >= 30, f'Handbook is missing route/engineering detail: {page.h3} subsections'
assert not re.search(r'sk-[a-zA-Z0-9]{15,}', content), 'Possible API key leaked'
assert '48 / 48 单元' in content
assert '9,600 个样本-方法案例' in content
assert 'CUB-200-2011</td><td>92.50%' in content
assert 'href="./annotation/' not in content
assert 'href="./annotation/workflow/' not in content
assert 'http://localhost:5173/annotation' in content

count = 0
for link in page.links:
    url = urlsplit(link)
    if url.scheme or url.netloc:
        continue
    target = (html_files[0].parent / unquote(url.path)).resolve() if url.path else html_files[0]
    assert target.is_relative_to(SITE), link
    if target.is_dir():
        target /= 'index.html'
    assert target.exists(), (link, target)
    if url.fragment:
        assert url.fragment in page.ids, (link, url.fragment)
    count += 1

required = {
    'chapter-0', 'chapter-1', 'chapter-2', 'chapter-3', 'chapter-4', 'chapter-5',
    'chapter-2-5', 'chapter-3-10', 'chapter-4-2', 'chapter-5-1',
}
assert required <= page.ids, f'Missing stable anchors: {required - page.ids}'
print(f'PASS: one page, 6 chapters, {page.h3} subsections, {count} local links/assets; anchors and evaluation report valid')
