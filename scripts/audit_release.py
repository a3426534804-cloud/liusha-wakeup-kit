"""Audit publishable files only; never print secret matches or file content."""
from pathlib import Path
import ast
import json
import re
import sys

ROOT=Path(__file__).resolve().parent.parent
TOP={'liusha','web','docs','deploy','examples','integrations','tests','scripts','.github'}
FILES={'README.md','LICENSE','SECURITY.md','CONTRIBUTING.md','pyproject.toml','.gitignore','.env.example'}
SKIP={'.git','__pycache__','.venv','venv','var','build','dist','node_modules'}
PATTERNS=[r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----',
          r'\bgh[pousr]_[A-Za-z0-9]{20,}',r'\bgithub_pat_[A-Za-z0-9_]{20,}',
          r'\bsk-ant-[A-Za-z0-9_-]{16,}',
          r'/home/[^/\s]+/(?:think-stream|Tidal_Echo|ClaudeProjects)(?:/|[\'\"])',
          r'wxid_[A-Za-z0-9_]{6,}',r'xwechat' + r'_files/']


def publishable():
    result=[]
    for p in ROOT.rglob('*'):
        rel=p.relative_to(ROOT)
        if any(x in SKIP or x.endswith('.egg-info') for x in rel.parts):continue
        if not p.is_file():continue
        if rel.parts[0] not in TOP and str(rel) not in FILES:
            raise ValueError('unexpected release path: '+rel.as_posix())
        if p.is_symlink():raise ValueError('symlink in release: '+rel.as_posix())
        if (p.suffix in {'.db','.sqlite','.key','.pem','.jsonl'}
                and rel.as_posix()!='examples/usage.synthetic.jsonl'):
            raise ValueError('runtime/private file in release: '+rel.as_posix())
        result.append(p)
    return sorted(result)


def main():
    files=publishable()
    for p in files:
        if p.suffix=='.png':continue
        content=p.read_text(encoding='utf-8')
        for pattern in PATTERNS:
            if re.search(pattern,content):raise ValueError('possible secret/private path in '+p.relative_to(ROOT).as_posix())
        if p.suffix=='.py':ast.parse(content,filename=p.name)
    raw=(ROOT/'examples/usage.synthetic.jsonl').read_text(encoding='utf-8')
    for line in raw.splitlines():
        row=json.loads(line)
        if row.get('model_used')!='demo-model':raise ValueError('fixture must be synthetic')
        if any(x in row for x in ('text','prompt','token','session_id','api_session')):raise ValueError('unexpected fixture field')
    print(f'RELEASE_AUDIT_OK: {len(files)} allowlisted files; no detected secrets/private paths/runtime data')
    return files


if __name__=='__main__':
    try:main()
    except (ValueError,UnicodeError,SyntaxError) as exc:print(str(exc),file=sys.stderr);sys.exit(1)
