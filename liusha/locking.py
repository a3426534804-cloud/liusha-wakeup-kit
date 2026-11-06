"""Shared Linux flock. ALL model-call participants must use the same path.

This is a standalone integration replacement, not a lock automatically applied
to an existing bridge. The operator must wire the bridge to this same lock.
"""
import os
from pathlib import Path
import threading


class GlobalClaudeLock:
    def __init__(self, path=None, **metadata):
        self.path=Path(path or os.environ.get('LIUSHA_CALL_LOCK','/tmp/liusha-claude-call.lock'))
        self.handle=None
        self.guard=threading.Lock()

    def acquire(self, blocking=False):
        import fcntl
        if not self.guard.acquire(blocking=blocking):return False
        self.path.parent.mkdir(parents=True,exist_ok=True)
        handle=None
        try:
            handle=self.path.open('a+')
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
            self.handle=handle
            return True
        except BlockingIOError:
            if handle:handle.close()
            self.guard.release()
            return False
        except Exception:
            if handle:handle.close()
            self.guard.release()
            raise

    def release(self):
        import fcntl
        if self.handle is None:raise RuntimeError('lock not held')
        try:fcntl.flock(self.handle.fileno(),fcntl.LOCK_UN)
        finally:self.handle.close();self.handle=None;self.guard.release()

    def locked(self):return self.guard.locked()

    def __enter__(self):
        if not self.acquire():raise RuntimeError('session busy')
        return self

    def __exit__(self,*args):self.release()
