import re
import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import quote


def history(store, key, query, run, Error):
    repo = store.repo(key)
    youngest = int(run('svnlook', 'youngest', str(repo)).strip())
    try:
        cursor = int(query.get('before', [str(youngest)])[0])
        limit = int(query.get('limit', ['30'])[0])
        if not 0 <= cursor <= youngest or not 1 <= limit <= 100:
            raise ValueError()
        start, end = (query.get(k, [''])[0] for k in ('from', 'to'))
        for value in (start, end):
            if value and date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        if start and end and start > end:
            raise ValueError()
    except (ValueError, TypeError):
        raise Error('无效的分页或日期参数') from None
    author = query.get('author', [''])[0].strip()
    if len(author) > 256:
        raise Error('作者名称过长')
    entries = []
    while cursor > 0 and len(entries) < limit:
        xml = ET.fromstring(run('svn', 'log', '--xml', '--non-interactive', '-r', f'{cursor}:1', '-l', '100', repo.as_uri() + '@HEAD'))
        batch = xml.findall('logentry')
        if not batch:
            cursor = 0
            break
        for item in batch:
            revision = int(item.get('revision'))
            cursor = revision - 1
            username, timestamp = item.findtext('author', ''), item.findtext('date', '')
            day = timestamp[:10]
            if (author and author.casefold() not in username.casefold()) or (start and day < start) or (end and day > end):
                continue
            entries.append({'revision': revision, 'author': username, 'date': timestamp, 'message': item.findtext('msg', '')})
            if len(entries) == limit:
                break
    return {'entries': entries, 'nextBefore': cursor if cursor > 0 else None}


def revision_files(store, key, revision, run, Error):
    repo = store.repo(key)
    if not re.fullmatch(r'[1-9][0-9]*', revision):
        raise Error('无效的版本号')
    root = ET.fromstring(run('svn', 'log', '--xml', '-v', '--non-interactive', '-r', revision, repo.as_uri() + '@HEAD'))
    return {'files': [{'path': p.text or '', 'action': p.get('action'), 'kind': p.get('kind', '')} for p in root.findall('.//paths/path')]}


def compare(store, key, query, run, Error):
    repo = store.repo(key)
    path = query.get('path', [''])[0].strip('/')
    old, new = (query.get(k, [''])[0] for k in ('old', 'new'))
    if (not path or '\\' in path or any(p in ('', '.', '..') for p in path.split('/'))
            or any(ord(c) < 32 for c in path) or not re.fullmatch(r'[0-9]+', old) or not re.fullmatch(r'[0-9]+', new)):
        raise Error('无效的文件路径或版本号')
    url = repo.as_uri() + '/' + quote(path, safe='/')
    result = run('svn', 'diff', '--non-interactive', '--old', url + '@' + old, '--new', url + '@' + new)
    return {'diff': result}
