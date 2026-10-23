"""Fixed local Git operations. Never called directly from external messages."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import struct
import subprocess
import tempfile
import time

GIT = '/usr/bin/git'
TIMEOUT = 10
LIMIT = 250_000
FILE_LIMIT = 2_000_000
OID = re.compile(r'^[0-9a-f]{40}$|^[0-9a-f]{64}$')


class GitFailure(ValueError):
    pass


class UncertainGit(ValueError):
    """A subprocess result or process-group termination could not be verified."""


def environment(extra=None):
    result = {'PATH': '/usr/bin:/bin', 'HOME': str(Path.home()), 'LC_ALL': 'C.UTF-8',
              'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
              'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0',
              'GIT_ATTR_NOSYSTEM': '1', 'GIT_NO_REPLACE_OBJECTS': '1',
              'GIT_NO_LAZY_FETCH': '1', 'GIT_PAGER': 'cat'}
    result.update(extra or {})
    return result


def run(project, *args, input=None, extra=None):
    command = [GIT, '--no-pager', '-c', 'core.hooksPath=/dev/null',
               '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=/dev/null',
               '-c', 'diff.external=', '-c', 'commit.gpgSign=false',
               '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
               '-c', 'core.autocrlf=false', '-c', 'color.ui=false',
               '-c', 'core.quotePath=false', '-C', str(project), *args]
    with tempfile.TemporaryFile() as source:
        if input is not None:
            source.write(input)
        source.seek(0)
        process = subprocess.Popen(command, env=environment(extra), stdin=source,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        output = bytearray()
        try:
            with selectors.DefaultSelector() as reader:
                reader.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + TIMEOUT
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not reader.select(remaining):
                        raise UncertainGit('Gitが時間切れです。結果未確認のため後続操作を禁止します。')
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > LIMIT:
                        raise UncertainGit('Gitの出力上限を超え、結果を確認できません。')
            process.wait(timeout=1)
        finally:
            try:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    raise UncertainGit('Git専用プロセス群の消滅を確認できません。')
            except (OSError, subprocess.SubprocessError) as error:
                raise UncertainGit('Git子プロセスの終了確認に失敗しました。') from error
            finally:
                process.stdout.close()
        if process.returncode < 0:
            raise UncertainGit('Gitがシグナルで終了しました。操作結果を確認できません。')
        if process.returncode:
            raise GitFailure('Git操作に失敗しました: ' + output.decode('utf-8', 'replace')[:2000])
        return bytes(output)


def text(project, *args, **kwargs):
    try:
        return run(project, *args, **kwargs).decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError('UTF-8で表示できない内容があるため操作を中止しました。') from error


def safe_name(name):
    return (isinstance(name, str) and name and '\0' not in name and
            not name.startswith('/') and all(p not in ('', '.', '..', '.git') for p in name.split('/')))


def read_regular(path, limit=FILE_LIMIT):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('通常ファイル以外は扱えません。')
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(data)))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
        raise ValueError('ファイルが初版のサイズ上限を超えています。')
    finally:
        os.close(descriptor)


def file_bytes(root, name):
    if not safe_name(name):
        raise ValueError('ファイル名を確認できません。')
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in name.split('/')[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        following = os.open(name.split('/')[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            if not stat.S_ISREG(os.fstat(following).st_mode):
                raise ValueError('通常ファイル以外は変更できません。')
            result = bytearray()
            while len(result) <= FILE_LIMIT:
                chunk = os.read(following, min(65536, FILE_LIMIT + 1 - len(result)))
                if not chunk:
                    return bytes(result)
                result.extend(chunk)
            raise ValueError('ファイルが初版のサイズ上限を超えています。')
        finally:
            os.close(following)
    finally:
        os.close(descriptor)


def settings_file(path):
    if not path.exists():
        return []
    read_regular(path)  # Reject symlinks, devices and oversized configuration.
    raw = text('/', 'config', '--file', str(path), '--no-includes', '--null', '--list')
    pairs = []
    for entry in raw.split('\0'):
        if not entry:
            continue
        key, _, value = entry.partition('\n')
        pairs.append((key.lower(), value))
    return pairs


class Repository:
    def __init__(self, project):
        self.root = Path(project).resolve(strict=True)
        self.uncertain = False
        marker = self.root / '.git'
        if marker.is_symlink():
            raise ValueError('Git管理情報のリンクは扱えません。')
        if marker.is_dir():
            self.gitdir = self.common = marker.resolve(strict=True)
            if (self.gitdir / 'commondir').exists():
                raise ValueError('通常リポジトリの外部common設定は扱えません。')
        elif marker.is_file():
            pointer = read_regular(marker).decode('utf-8').strip()
            if not pointer.startswith('gitdir: '):
                raise ValueError('Git管理情報が不正です。')
            self.gitdir = (self.root / pointer[8:]).resolve(strict=True)
            self.common = (self.gitdir / read_regular(self.gitdir / 'commondir').decode().strip()).resolve(strict=True)
            if (self.gitdir.parent.name != 'worktrees' or self.gitdir.parent.parent != self.common or
                    Path(read_regular(self.gitdir / 'gitdir').decode().strip()).resolve() != marker):
                raise ValueError('worktree管理情報が一致しません。')
        else:
            raise ValueError('プロジェクト直下にGit管理情報がありません。')
        # Management data is outside Codex's writable files, but still validate
        # it before the stronger Host lets Git follow any paths or load settings.
        checked = set()
        for directory in (self.common, self.gitdir):
            for base, dirs, files in os.walk(directory, followlinks=False):
                dirs[:] = [d for d in dirs if d not in ('hooks', 'worktrees')]
                for name in dirs + files:
                    item = Path(base) / name
                    if item in checked:
                        continue
                    checked.add(item)
                    mode = item.lstat().st_mode
                    if not stat.S_ISDIR(mode) and not stat.S_ISREG(mode):
                        raise ValueError('Git管理情報のリンク・特殊ファイルは扱えません。')
                    if len(checked) > 100000:
                        raise ValueError('Git管理情報の検査上限を超えました。')
        self.settings = settings_file(self.common / 'config') + settings_file(self.gitdir / 'config.worktree')
        allowed = {'core.repositoryformatversion', 'core.filemode', 'core.bare',
                   'core.logallrefupdates', 'core.ignorecase', 'core.precomposeunicode',
                   'core.hookspath', 'commit.gpgsign', 'user.name', 'user.email',
                   'extensions.worktreeconfig', 'extensions.objectformat'}
        for key, value in self.settings:
            if key in allowed:
                if key == 'core.bare' and value != 'false':
                    raise ValueError('bareリポジトリは扱えません。')
                continue
            if re.fullmatch(r'remote\..+\.(url|fetch|pushurl)|branch\..+\.(remote|merge|vscode-merge-base)', key):
                if key.endswith(('.url', '.pushurl')) and ('::' in value or value.startswith(('-', '|'))):
                    raise ValueError('remote外部コマンド形式は扱えません。')
                continue  # Data only: no remote operation is exposed.
            raise ValueError(f'初版では許可しないGit設定です: {key}')
        for base, names in ((self.common, ['objects/info/alternates', 'objects/info/http-alternates',
                                          'info/grafts', 'shallow']),
                            (self.gitdir, ['sharedindex', 'sparse-checkout'])):
            if any((base / name).exists() for name in names):
                raise ValueError('外部オブジェクト・特殊インデックス構成は扱えません。')
        if any((self.common / 'objects/pack').glob('*.promisor')) or any((self.common / 'refs/replace').glob('*')):
            raise ValueError('partial clone・replace参照は扱えません。')
        # Do not permit object/index symlinks to redirect the stronger Host.
        for path in (self.common / 'objects', self.gitdir / 'index', self.common / 'refs'):
            if path.is_symlink():
                raise ValueError('Git管理情報のシンボリックリンクは扱えません。')
        if self.call('rev-parse', '--show-toplevel').strip() != str(self.root):
            raise ValueError('Gitルートが登録対象と一致しません。')
        if self.call('rev-parse', '--absolute-git-dir').strip() != str(self.gitdir):
            raise ValueError('Git管理情報の実体が一致しません。')
        if self.call('rev-parse', '--path-format=absolute', '--git-common-dir').strip() != str(self.common):
            raise ValueError('Git共通管理情報の実体が一致しません。')
        if self.call('for-each-ref', '--format=%(refname)', 'refs/replace').strip():
            raise ValueError('replace参照は扱えません。')
        self.validate_attributes()

    def call(self, *args, **kwargs):
        return text(self.root, *args, **kwargs)

    def entries(self, extra=None):
        result = []
        for record in self.call('ls-files', '--stage', '-z', extra=extra).split('\0'):
            if not record:
                continue
            meta, name = record.split('\t', 1)
            mode, oid, stage = meta.split()
            if not safe_name(name):
                raise ValueError('安全に扱えないインデックスパスです。')
            result.append((mode, oid, stage, name))
        return result

    def validate_attributes(self):
        attribute_data = []
        count = 0
        for base, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d != '.git' and not (Path(base) / d).is_symlink())
            count += len(dirs) + len(files)
            if count > 50000:
                raise ValueError('属性検査の走査上限を超えました。')
            if '.gitattributes' in files:
                name = str((Path(base) / '.gitattributes').relative_to(self.root))
                attribute_data.append((name, file_bytes(self.root, name)))
        for directory in dict.fromkeys([self.common, self.gitdir]):
            info = directory / 'info/attributes'
            if info.exists() or info.is_symlink():
                attribute_data.append((str(info), read_regular(info)))
        for mode, oid, stage, name in self.entries():
            if name.split('/')[-1] == '.gitattributes':
                if mode != '100644' and mode != '100755' or stage != '0':
                    raise ValueError('属性ファイルの形式を確認できません。')
                attribute_data.append(('index:' + name, run(self.root, 'cat-file', 'blob', oid)))
        allowed = {'text', '-text', '!text', 'text=auto', 'eol=lf', 'eol=crlf', '-eol', '!eol',
                   'diff', '-diff', '!diff', 'binary', '-binary', '!binary'}
        for name, data in attribute_data:
            for line in data.decode('utf-8').splitlines():
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                parts = shlex.split(line, comments=False, posix=True)
                if not parts or parts[0].startswith('[attr]') or any(p not in allowed for p in parts[1:]):
                    raise ValueError('初版では許可しない.gitattributes指定があります。')
        self.attribute_digest = hashlib.sha256(b'\0'.join(name.encode() + b'\0' + data for name, data in attribute_data)).hexdigest()

    def changes(self):
        rows = self.call('status', '--porcelain=v1', '-z', '--untracked-files=all').split('\0')
        files, i = [], 0
        while i < len(rows) and rows[i]:
            row = rows[i]
            if len(row) < 4 or row[2] != ' ':
                raise ValueError('Git状態を確認できません。')
            code, name, original = row[:2], row[3:], None
            if 'R' in code or 'C' in code:
                i += 1
                original = rows[i]
            if not safe_name(name) or original is not None and not safe_name(original):
                raise ValueError('Gitファイル名を確認できません。')
            files.append((code, name, original))
            i += 1
            if len(files) > 100:
                raise ValueError('変更ファイルが100件を超えています。')
        return files

    def branch(self):
        ref = self.call('symbolic-ref', '-q', 'HEAD').strip()
        if not ref.startswith('refs/heads/') or '\n' in ref:
            raise ValueError('通常のローカルブランチを選択してください。')
        path = self.common / ref
        if path.is_file() and read_regular(path).startswith(b'ref:'):
            raise ValueError('ブランチの間接参照は扱えません。')
        # Empty repository: distinguish missing ref from other rev-parse failures.
        refs = self.call('for-each-ref', '--format=%(refname) %(objectname)', ref).splitlines()
        old = next((line.split(' ', 1)[1] for line in refs if line.startswith(ref + ' ')), None)
        if old is None and path.exists():
            raise ValueError('既存ブランチの状態を解決できません。')
        if old is not None and not OID.fullmatch(old):
            raise ValueError('ブランチOIDが不正です。')
        return ref, old

    def writable(self):
        for name in ['MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer', 'BISECT_START']:
            if (self.gitdir / name).exists():
                raise ValueError('進行中のGit操作を外部で完了してください。')
        index = self.gitdir / 'index'
        if index.exists():
            data = read_regular(index, 16_000_000)
            if data[:4] != b'DIRC' or len(data) < 12:
                raise ValueError('インデックス形式が不正です。')
            version, count = struct.unpack('>II', data[4:12])
            if version not in (2, 3):
                raise ValueError('初版はindex v2/v3だけに対応します。')
            hash_size = 32 if ('extensions.objectformat', 'sha256') in self.settings else 20
            content_end = len(data) - hash_size
            if content_end < 12:
                raise ValueError('インデックス構造を確認できません。')
            offset = 12
            for _ in range(count):
                start = offset
                fixed_end = offset + 42 + hash_size
                if fixed_end > content_end:
                    raise ValueError('インデックス構造を確認できません。')
                flags = struct.unpack('>H', data[offset + 40 + hash_size:fixed_end])[0]
                offset = fixed_end + (2 if flags & 0x4000 else 0)
                if offset > content_end:
                    raise ValueError('インデックス構造を確認できません。')
                try:
                    offset = data.index(b'\0', offset, content_end) + 1
                except ValueError as error:
                    raise ValueError('インデックス構造を確認できません。') from error
                offset = start + ((offset - start + 7) // 8 * 8)
                if offset > content_end:
                    raise ValueError('インデックス構造を確認できません。')
            while offset < content_end:
                if offset + 8 > content_end:
                    raise ValueError('インデックス構造を確認できません。')
                signature, length = struct.unpack('>4sI', data[offset:offset + 8])
                if signature in (b'link', b'sdir'):
                    raise ValueError('split/sparse indexは初版で扱えません。')
                offset += 8 + length
                if offset > content_end:
                    raise ValueError('インデックス構造を確認できません。')
            if offset != content_end:
                raise ValueError('インデックス構造を確認できません。')
        for mode, oid, stage, name in self.entries():
            if mode not in ('100644', '100755') or stage != '0':
                raise ValueError('競合・submodule・リンクを含むインデックスは変更できません。')
        self.branch()

    def fingerprint(self):
        rows = self.changes()
        files = {}
        for _, name, original in rows:
            for path in (name, original):
                if path is None:
                    continue
                try:
                    data = file_bytes(self.root, path)
                    mode = (self.root / path).stat().st_mode & 0o111
                    files[path] = [hashlib.sha256(data).hexdigest(), mode]
                except FileNotFoundError:
                    files[path] = None
        index = self.gitdir / 'index'
        ref, old = self.branch()
        values = [[str(p), p.stat().st_dev, p.stat().st_ino] for p in (self.root, self.gitdir, self.common)] + [self.settings,
                  self.attribute_digest, ref, old, rows, files,
                  hashlib.sha256(read_regular(index, 16_000_000)).hexdigest() if index.exists() else None]
        return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    @contextlib.contextmanager
    def index_lock(self):
        path = self.gitdir / 'index.lock'
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            yield descriptor
        finally:
            os.close(descriptor)
            if path.exists():
                path.unlink()

    def tree(self, temporary=False):
        if not temporary:
            return self.call('write-tree').strip()
        with tempfile.TemporaryDirectory(prefix='focodex-index-') as directory:
            target = Path(directory) / 'index'
            index = self.gitdir / 'index'
            if index.exists():
                target.write_bytes(read_regular(index, 16_000_000))
            return self.call('write-tree', extra={'GIT_INDEX_FILE': str(target)}).strip()

    def identity(self):
        identity = {}
        for path in (Path.home() / '.config/git/config', Path.home() / '.gitconfig'):
            for key, value in settings_file(path):
                if key in ('user.name', 'user.email'):
                    identity[key] = value
        identity.update({key: value for key, value in self.settings if key in ('user.name', 'user.email')})
        name, email = identity.get('user.name', ''), identity.get('user.email', '')
        if (any(not value or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 or c in '<>' for c in value)
                for value in (name, email)) or any(c.isspace() for c in email) or '@' not in email):
            raise ValueError('Gitのuser.nameとuser.emailをincludeなしの設定へ登録してください。')
        return {'name': name, 'email': email}

    def stage(self, names, expected):
        self.writable()
        if self.fingerprint() != expected:
            raise ValueError('表示後に変更されました。Git状態を再取得してください。')
        if (not isinstance(names, list) or not 1 <= len(names) <= 100 or
                any(not isinstance(name, str) for name in names) or len(set(names)) != len(names)):
            raise ValueError('ステージするファイルを選択してください。')
        known = {name: (code, original) for code, name, original in self.changes()}
        paths = set()
        for name in names:
            if name not in known:
                raise ValueError('表示にないファイルはステージできません。')
            code, original = known[name]
            for item in [name] + ([original] if original else []):
                try:
                    file_bytes(self.root, item)
                except FileNotFoundError:
                    pass
                paths.add(item)
        with self.index_lock() as descriptor, tempfile.TemporaryDirectory(prefix='focodex-stage-') as directory:
            if self.fingerprint() != expected:
                raise ValueError('ステージ直前に変更されました。再取得してください。')
            target = Path(directory) / 'index'
            actual = self.gitdir / 'index'
            if actual.exists():
                target.write_bytes(read_regular(actual, 16_000_000))
            extra = {'GIT_INDEX_FILE': str(target)}
            before_entries = {row[3]: row[:3] for row in self.entries()}
            self.call('add', '-A', '--', *[':(literal)' + path for path in sorted(paths)], extra=extra)
            after_entries = {row[3]: row[:3] for row in self.entries(extra=extra)}
            present = sorted(paths & after_entries.keys())
            if present:
                # Copying an index changes its timestamp and can hide Git's racy
                # stat-cache condition. Force Git to hash the selected files.
                self.call('add', '--renormalize', '--', *[':(literal)' + path for path in present], extra=extra)
                after_entries = {row[3]: row[:3] for row in self.entries(extra=extra)}
            changed = {name for name in before_entries.keys() | after_entries.keys()
                       if before_entries.get(name) != after_entries.get(name)}
            if not changed <= paths or any(mode not in ('100644', '100755') or stage != '0'
                                          for mode, oid, stage in after_entries.values()):
                raise ValueError('選択外または非対応のステージ結果を拒否しました。')
            fresh = Repository(self.root)
            if fresh.fingerprint() != expected:
                raise ValueError('ステージ中に変更されました。再取得してください。')
            data = read_regular(target, 16_000_000)
            self.uncertain = True
            with os.fdopen(os.dup(descriptor), 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(self.gitdir / 'index.lock', actual)
            parent = os.open(self.gitdir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
            fresh = Repository(self.root)
            fresh.writable()
            if {row[3]: row[:3] for row in fresh.entries()} != after_entries:
                raise UncertainGit('ステージ結果を確認できません。')
            self.uncertain = False

    def review(self, message):
        self.writable()
        if not isinstance(message, str) or not message.strip() or len(message.encode('utf-16-le')) > 40000 or '\0' in message:
            raise ValueError('Commitメッセージは1〜20000文字で指定してください。')
        before = self.fingerprint()
        tree = self.tree(temporary=True)
        ref, old = self.branch()
        base = old or self.call('hash-object', '-w', '-t', 'tree', '--stdin', input=b'').strip()
        diff = self.call('diff', '--no-ext-diff', '--no-textconv', '--no-color', '--binary', base, tree, '--')
        if not diff:
            raise ValueError('ステージ済みの変更がありません。')
        if 'GIT binary patch' in diff.splitlines() or '\x00' in diff:
            raise ValueError('初版のCommitは表示可能なテキスト差分だけに対応します。')
        identity = self.identity()
        fresh = Repository(self.root)
        if fresh.fingerprint() != before:
            raise ValueError('確認中に変更されました。Git状態を再取得してください。')
        return {'fingerprint': before, 'tree': tree, 'ref': ref, 'parent': old,
                'message': message, 'identity': identity, 'diff': diff}

    def commit(self, review):
        self.writable()
        with self.index_lock():
            if (self.fingerprint() != review['fingerprint'] or self.tree(temporary=True) != review['tree'] or
                    self.identity() != review['identity']):
                raise ValueError('最終確認後に変更されました。再確認してください。')
            identity = review['identity']
            extra = {'GIT_AUTHOR_NAME': identity['name'], 'GIT_AUTHOR_EMAIL': identity['email'],
                     'GIT_COMMITTER_NAME': identity['name'], 'GIT_COMMITTER_EMAIL': identity['email']}
            args = ['commit-tree', review['tree']]
            if review['parent']:
                args += ['-p', review['parent']]
            self.uncertain = True
            # Commit-tree terminates the message with a newline as Git normally does.
            message = review['message'].encode('utf-8')
            if not message.endswith(b'\n'):
                message += b'\n'
            new = self.call(*args, '--no-gpg-sign', '-F', '-', input=message, extra=extra).strip()
            if not OID.fullmatch(new):
                raise UncertainGit('作成したCommitのOIDを確認できません。')
            raw = run(self.root, 'cat-file', 'commit', new)
            header, _, body = raw.partition(b'\n\n')
            headers = header.decode().splitlines()
            parents = [line[7:] for line in headers if line.startswith('parent ')]
            if (headers[0] != 'tree ' + review['tree'] or parents != ([review['parent']] if review['parent'] else []) or
                    body != message or any(line.startswith('gpgsig ') for line in headers) or
                    not any(line.startswith(f"author {identity['name']} <{identity['email']}> ") for line in headers) or
                    not any(line.startswith(f"committer {identity['name']} <{identity['email']}> ") for line in headers)):
                raise UncertainGit('Commitの内容が確認済みの内容と一致しません。')
            # Recheck project data after object creation; never follow a newly selected HEAD.
            fresh = Repository(self.root)
            if fresh.fingerprint() != review['fingerprint']:
                self.uncertain = False  # Object exists, but no ref-update was attempted.
                raise ValueError('Commit直前に変更されました。再確認してください。')
            expected = review['parent'] or '0' * len(new)
            try:
                self.call('update-ref', '--no-deref', '-m', 'Focodex Commit', review['ref'], new, expected)
            except GitFailure:
                current = self.call('for-each-ref', '--format=%(refname) %(objectname)', review['ref'])
                if f"{review['ref']} {new}\n" in current:
                    raise UncertainGit('参照更新の結果が不確実です。再実行しないでください。')
                self.uncertain = False
                raise ValueError('ブランチ更新は完了しませんでした。Git状態を再取得してください。')
            if self.call('rev-parse', review['ref']).strip() != new:
                raise UncertainGit('Commit後のブランチ状態を確認できません。')
            fresh = Repository(self.root)
            fresh.writable()
            fresh.fingerprint()
            self.uncertain = False
            return {'commit': new, 'ref': review['ref'], 'tree': review['tree'], 'message': review['message']}
