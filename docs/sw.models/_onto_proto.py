import re, os, sys, collections
ROOT = sys.argv[1]  # .../decisional/dia5
NS_STRIP = re.compile(r'<\?.*?\?>|<!--.*?-->', re.S)

def strip_lua(src):
    """drop comments, blank out string contents (keeps structure)"""
    out = []; i = 0; n = len(src)
    while i < n:
        c = src[i]
        if src.startswith('--', i):
            m = re.match(r'--\[(=*)\[', src[i:i+20])
            if m:
                end = src.find(']' + m.group(1) + ']', i)
                i = n if end < 0 else end + len(m.group(1)) + 2
            else:
                j = src.find('\n', i); i = n if j < 0 else j
            continue
        m = re.match(r'\[(=*)\[', src[i:i+20])
        if m:
            end = src.find(']' + m.group(1) + ']', i)
            out.append('""'); i = n if end < 0 else end + len(m.group(1)) + 2; continue
        if c in '"\'':
            j = i + 1
            while j < n and src[j] != c and src[j] != '\n':
                if src[j] == '\\': j += 1
                j += 1
            out.append('""'); i = j + 1; continue
        out.append(c); i += 1
    return ''.join(out)

TOK = re.compile(r'[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*\s*=(?!=)|[A-Za-z_]\w*|[{}]|==|~=|<=|>=|\S')

def lua_methods(path):
    """keys of the class table: brace depth 1 (or X.name at depth 0), outside any function/block body"""
    src = strip_lua(open(path, encoding='utf-8', errors='replace').read())
    keys = {}
    brace = 0; blocks = []; pend_do = 0
    for m in TOK.finditer(src):
        t = m.group(0)
        if t.endswith('=') and t[0].isalpha() or t[0] == '_' and t.endswith('='):
            lhs = re.sub(r'\s', '', t[:-1])
            if not blocks:
                rhs = src[m.end():m.end() + 60].lstrip()
                form = ('function' if rhs.startswith('function') else
                        'startStopAction' if rhs.startswith('spark.startStopAction') else
                        'action' if rhs.startswith('spark.action') else 'alias')
                if brace == 1 and '.' not in lhs: keys.setdefault(lhs, form)
                elif brace == 0 and lhs.count('.') == 1: keys.setdefault(lhs.split('.')[1], form)
            continue
        if t == '{': brace += 1
        elif t == '}': brace -= 1
        elif t in ('function', 'if', 'repeat'): blocks.append(t)
        elif t in ('while', 'for'): pend_do += 1; blocks.append(t)
        elif t == 'do':
            if pend_do: pend_do -= 1
            else: blocks.append('do')
        elif t in ('end', 'until'):
            if blocks: blocks.pop()
    return keys

ents = {}
for r, d, fs in os.walk(ROOT):
    for f in fs:
        if not f.endswith('.xml'): continue
        p = os.path.join(r, f); t = open(p, encoding='utf-8', errors='replace').read()
        t2 = NS_STRIP.sub('', t)
        m = re.search(r'<([A-Za-z][\w.-]*)\b([^>]*)>', t2)
        kind = m.group(1)
        if kind not in ('class', 'interface', 'role'): continue
        name = re.search(r'\bname="([^"]+)"', m.group(2)).group(1)
        e = dict(kind=kind, path=os.path.relpath(p, ROOT).replace('\\', '/'))
        e['cabstract'] = 'abstract="true"' in m.group(2)
        e['extends'] = re.findall(r'<extends>\s*([^<\s]+)\s*</extends>', t2)
        e['implements'] = re.findall(r'<implements>\s*([^<\s]+)\s*</implements>', t2)
        bt = re.search(r'<body-type>(.*?)</body-type>', t2, re.S)
        e['body'] = re.findall(r'type="([^"]+)"', bt.group(1)) if bt else []
        mm = re.search(r'<methods>(.*)</methods>', t2, re.S)
        tags = re.findall(r'<method\s([^>]*)>', mm.group(1)) if mm else []
        e['declared'] = [re.search(r'name="([^"]+)"', a).group(1) for a in tags]
        e['abstract'] = [re.search(r'name="([^"]+)"', a).group(1) for a in tags if 'abstract="true"' in a]
        e['method_attrs'] = [re.sub(r'="[^"]*"', '', a).split() for a in tags]
        e['ctor'] = bool(re.search(r'<constructor', t2))
        lp = p[:-4] + '.lua'
        e['impl'] = lua_methods(lp) if os.path.exists(lp) else None
        ents[name] = e

print(collections.Counter(e['kind'] for e in ents.values()))
classes = {n: e for n, e in ents.items() if e['kind'] == 'class'}
ifaces = {n: e for n, e in ents.items() if e['kind'] == 'interface'}
print('method attrs:', collections.Counter(a for e in ents.values() for l in e['method_attrs'] for a in l))
print('multi extends:', [(n, e['extends']) for n, e in ents.items() if len(e['extends']) > 1][:10])
def chain(n):
    out = []
    while n in classes:
        out.append(n); ex = classes[n]['extends']; n = ex[0] if ex else None
    return out, n
meta = {n for n in classes if 'sparkle.TaskMetadata' in [chain(n)[1]]}
print('task-metadata classes:', len(meta), 'world classes:', len(classes) - len(meta))
print('meta dirs:', collections.Counter(classes[n]['path'].rsplit('/', 1)[0] for n in meta).most_common(8))
unimpl = []; extra = []
for n, e in classes.items():
    impl = set(e['impl'] or {})
    unimpl += [(n, m) for m in e['declared'] if m not in impl and m not in e['abstract']]
    extra += [(n, m) for m in impl if m not in e['declared'] and m != '__init']
print('declared-not-in-own-lua (non-abstract):', len(unimpl), unimpl[:10])
print('lua keys not declared in own xml:', len(extra), extra[:25])
def all_impl(n):
    s = set(); ch, ext = chain(n)
    for c in ch: s |= set(classes[c]['impl'] or {})
    return s
def iface_methods(i, seen=None):
    seen = seen if seen is not None else set()
    if i in seen or i not in ifaces: return set()
    seen.add(i); s = set(ifaces[i]['declared'])
    for x in ifaces[i]['extends']: s |= iface_methods(x, seen)
    return s
miss = [(n, i, m) for n, e in classes.items() for i in e['implements'] for m in iface_methods(i) if m not in all_impl(n)]
print('iface methods missing from class chain lua:', len(miss), miss[:10])
direct = collections.Counter(i for c in classes.values() for i in c['implements'])
print('iface direct implementers 0/1:', [(direct[i], i) for i in ifaces if direct[i] <= 1])
print('ifaces extended by ifaces:', collections.Counter(x for e in ifaces.values() for x in e['extends']))
print('depth max:', sorted(((len(chain(n)[0]), n) for n in classes), reverse=True)[:4])
print('impl forms:', collections.Counter(f for e in classes.values() for f in (e['impl'] or {}).values()))
print('classes with empty lua:', [n for n, e in classes.items() if not e['impl']])
print('interfaces with .lua:', [n for n, e in ifaces.items() if e['impl'] is not None])
for n, e in sorted(ents.items()):
    if e['kind'] == 'role':
        print(' role', n, 'ext', e['extends'], 'body', e['body'], 'lua', sorted(e['impl'] or {}) if e['impl'] is not None else None, 'decl', e['declared'][:5])
# methods defined in >1 world class (same name)
cnt = collections.defaultdict(list)
for n, e in classes.items():
    if n in meta: continue
    for m in (e['impl'] or {}): cnt[m].append(n)
dup = {m: v for m, v in cnt.items() if len(v) > 1 and m != '__init'}
print('method names in >1 world class:', len(dup), sorted(dup.items(), key=lambda x: -len(x[1]))[:5])

# ---- call sites by method name (upper bound) ----
import glob
INTEG = 'C:/MASARepo/sw.sword/data/app-data/resources/integration'
def lua_files(root):
    for r, d, fs in os.walk(root):
        for f in fs:
            if f.endswith('.lua'): yield os.path.join(r, f)
calls = collections.defaultdict(lambda: collections.Counter())
for scope, root in (('models', ROOT), ('integration', INTEG)):
    for p in lua_files(root):
        s = strip_lua(open(p, encoding='utf-8', errors='replace').read())
        for m in re.finditer(r':\s*([A-Za-z_]\w*)\s*[\(\{"]', s):
            calls[m.group(1)][scope] += 1
allm = set()
for n, e in ents.items():
    if e['kind'] in ('class', 'interface'): allm |= set(e['declared'])
never = sorted(m for m in allm if not calls[m])
print('declared method names:', len(allm), 'never called by name anywhere:', len(never), never[:30])
only_integ = sorted(m for m in allm if calls[m] and not calls[m]['models'])
print('called only from integration:', len(only_integ), only_integ[:20])
# kind of file calling (skill/task/query/class/role) - check task lua none

print('__init without ctor:', [n for n, e in classes.items() if '__init' in (e['impl'] or {}) and not e['ctor']])
print('ctor without __init:', [n for n, e in classes.items() if e['ctor'] and '__init' not in (e['impl'] or {})])
def chain_decl(n):
    s = {}
    for c in reversed(chain(n)[0]):
        for m in classes[c]['declared']: s[m] = m in classes[c]['abstract']
    return s
ruleA = []
for n, e in classes.items():
    if e['cabstract']: continue
    d = chain_decl(n); ifs = set()
    for c in chain(n)[0]: ifs |= set(classes[c]['implements'])
    for i in ifs:
        for m in iface_methods(i):
            if m not in d or d[m]: ruleA.append((n, i, m))
print('rule A (concrete class, iface method not redeclared non-abstract in chain XML):', len(ruleA), ruleA[:10])
print('abstract classes:', [n for n, e in classes.items() if e['cabstract']])
