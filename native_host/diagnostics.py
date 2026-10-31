"""Private, bounded operation diagnostics without user content."""
import json
import os
from pathlib import Path
import stat
import time
import uuid


MAX_LOG_BYTES = 1024 * 1024
MAX_EVENT_BYTES = 8 * 1024


def _state_directory():
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'focodex'


def operation_diagnostic_path():
    """Return a private path or None; never follow an unsafe log path."""
    directory = _state_directory()
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        status = directory.lstat()
        if (not stat.S_ISDIR(status.st_mode) or stat.S_ISLNK(status.st_mode) or
                status.st_uid != os.geteuid() or status.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
            raise OSError('unsafe diagnostic directory')
        path = directory / 'operation-diagnostics.jsonl'
        if path.exists():
            status = path.lstat()
            if (not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode) or
                    status.st_uid != os.geteuid() or status.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
                raise OSError('unsafe diagnostic log')
        return path
    except OSError:
        return None


def new_trace_id():
    return str(uuid.uuid4())


def _rotate(path):
    try:
        if path.exists() and path.stat().st_size >= MAX_LOG_BYTES:
            previous = path.with_name(path.name + '.1')
            os.replace(path, previous)
    except OSError:
        return False
    return True


def write_operation_diagnostic(event):
    """Best-effort append of a content-free operation event.

    A diagnostic write must not keep a Codex process alive or change its safety
    decision.  Callers receive False and expose only that fact to the UI.
    """
    path = operation_diagnostic_path()
    if path is None or not _rotate(path):
        return False
    payload = {'version': 1, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **event}
    try:
        data = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode() + b'\n'
    except (TypeError, ValueError):
        return False
    if len(data) > MAX_EVENT_BYTES:
        return False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        return False
    return True
