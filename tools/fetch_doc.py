"""관계자가 준 문서 링크(html/pdf)를 받아 knowledge/docs/<이름>.md로 텍스트 저장. python tools/fetch_doc.py <URL> <이름>"""
import html
import pathlib
import re
import sys

import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
url, name = sys.argv[1], sys.argv[2]
raw = ROOT / "docs" / "contest" / "files"
raw.mkdir(parents=True, exist_ok=True)
body = urllib.request.urlopen(url, timeout=30).read()

if url.endswith(".pdf"):
    (raw / f"{name}.pdf").write_bytes(body)
    import pypdf
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(raw / f"{name}.pdf").pages)
else:
    (raw / f"{name}.html").write_bytes(body)
    s = body.decode("utf-8", "replace")
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|li|h\d|tr|div|section|table)>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", " | ", s)
    text = html.unescape(re.sub(r"<[^>]+>", "", s))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)

out = ROOT / "knowledge" / "docs" / f"{name}.md"
out.write_text(f"<!-- {url} -->\n{text.strip()}\n", encoding="utf-8")
print(f"{out.name}: {len(text)}자")
