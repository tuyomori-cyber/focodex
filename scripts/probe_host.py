#!/usr/bin/env python3
"""Exercise the real host's Firefox-compatible framing without a browser."""
import argparse
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def write_message(process, message):
    body = json.dumps(message, ensure_ascii=False).encode()
    process.stdin.write(struct.pack('=I', len(body)) + body)
    process.stdin.flush()


def read_message(process, timeout=190):
    deadline = time.monotonic() + timeout
    def exact(size):
        result = bytearray()
        while len(result) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise TimeoutError('Hostの応答待ちが時間切れになりました。')
            part = os.read(process.stdout.fileno(), size - len(result))
            if not part:
                raise EOFError('Hostが応答前に終了しました。')
            result.extend(part)
        return result
    size = struct.unpack('=I', exact(4))[0]
    if size > 1024 * 1024:
        raise ValueError('Host応答が1 MiBを超えました。')
    return json.loads(exact(size))


def launch(env=None):
    return subprocess.Popen([sys.executable, str(ROOT / 'native_host/focodex.py')],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, bufsize=0)


def close(process):
    if process.poll() is None:
        process.stdin.close()
        try:
            process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=6)
    if not process.stdin.closed:
        process.stdin.close()
    process.stdout.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path)
    parser.add_argument('--project', default='focodex')
    parser.add_argument('--model', help='一覧にあるモデルを明示選択')
    parser.add_argument('--list-models', action='store_true', help='モデル一覧の取得のみ行う')
    args = parser.parse_args()
    env = dict(os.environ)
    if args.config:
        env['FOCODEX_CONFIG'] = str(args.config.resolve())
    process = launch(env)
    try:
        write_message(process, {'type': 'projects', 'id': 'projects'})
        message = read_message(process)
        if message.get('type') != 'projects':
            raise RuntimeError(message)
        print(json.dumps(message, ensure_ascii=False))
        if args.list_models or args.model:
            write_message(process, {'type': 'models', 'id': 'models', 'projectId': args.project})
            while True:
                message = read_message(process)
                print(json.dumps(message, ensure_ascii=False))
                if message['type'] != 'started':
                    break
            if message['type'] != 'models':
                sys.exit(1)
            if args.list_models:
                sys.exit(0)
        request = {'type': 'run', 'id': 'probe', 'projectId': args.project,
            'text': 'ツールを使わず、ファイルの読み書きやコマンド実行をせずに、「Focodex接続成功」とだけ返答してください。'}
        if args.model:
            request['model'] = args.model
        write_message(process, request)
        while True:
            message = read_message(process)
            print(json.dumps(message, ensure_ascii=False))
            if message['type'] in ('result', 'error'):
                success = message.get('status') == 'completed' and bool(message.get('text'))
                sys.exit(0 if success else 1)
    finally:
        close(process)
