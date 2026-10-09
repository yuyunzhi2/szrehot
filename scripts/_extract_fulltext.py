import json, re, html, io, sys

raw = io.open(r'C:\Users\Administrator\.local\share\opencode\tool-output\tool_05f3c8224001Eq71207M69NdVb', encoding='utf-8').read()

# Locate the JSON results array: it starts with [{"url"
marker = '[{"url"'
start = raw.find(marker)
if start == -1:
    sys.stderr.write('marker not found\n')
    sys.exit(1)
# find the end: last ']' after start
end = raw.rfind(']')
data = raw[start:end+1]
res = json.loads(data)
print('parsed results:', len(res))

out = []
for r in res:
    content = r.get('content', '')
    tm = re.search(r'<title>(.*?)</title>', content, re.DOTALL)
    title = html.unescape(re.sub(r'<[^>]+>', '', tm.group(1))).strip() if tm else r.get('url', '')
    body = re.sub(r'<script.*?</script>', ' ', content, flags=re.DOTALL)
    body = re.sub(r'<style.*?</style>', ' ', body, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '\n', body)
    text = html.unescape(text)
    text = re.sub(r'[ \t\u3000]+', ' ', text)
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    dedup = []
    for l in lines:
        if not dedup or dedup[-1] != l:
            dedup.append(l)
    out.append({'url': r['url'], 'title': title, 'text': '\n'.join(dedup), 'content_length': r.get('content_length', 0)})

json.dump(out, open('_fulltext.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('written _fulltext.json, total chars:', sum(len(o['text']) for o in out))
