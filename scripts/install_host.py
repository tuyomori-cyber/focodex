#!/usr/bin/env python3
"""Install this checkout's host and seed trusted, user-local configuration."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def install(home, codex):
    home = Path(home).resolve()
    executable = Path(codex).absolute()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError('Codex実行ファイルが見つかりません。--codexで絶対パスを指定してください。')
    target = home / '.local/lib/focodex'
    target.mkdir(parents=True, exist_ok=True)
    host = target / 'focodex.py'
    shutil.copyfile(ROOT / 'native_host/focodex.py', host)
    host.chmod(0o700)
    git_module = target / 'git_ops.py'
    shutil.copyfile(ROOT / 'native_host/git_ops.py', git_module)
    git_module.chmod(0o600)
    launcher = target / 'launch'
    # JSON quoting is not shell escaping. Quote executable paths with shlex.
    import shlex
    launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(host)) + ' "$@"\n')
    launcher.chmod(0o700)
    config = home / '.config/focodex/config.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    if not config.exists():
        with config.open('x') as stream:
            json.dump({'codex': str(executable), 'projects': {
                'focodex': {'name': 'Focodex', 'path': str(ROOT)}}}, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        config.chmod(0o600)
    manifest = home / '.mozilla/native-messaging-hosts/focodex.json'
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({'name': 'focodex', 'description': 'Focodex connection probe',
        'path': str(launcher), 'type': 'stdio',
        'allowed_extensions': ['focodex@foxbook.example']}, indent=2) + '\n')
    manifest.chmod(0o600)
    print(f'Host登録: {manifest}\n設定（既存は保持）: {config}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path.home(), help='登録先ホーム。試験時は一時ディレクトリを指定')
    parser.add_argument('--codex', default=shutil.which('codex'))
    args = parser.parse_args()
    if not args.codex:
        parser.error('codexがPATH上にありません。--codexを指定してください。')
    install(args.home, args.codex)
