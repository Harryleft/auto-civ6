"""Nonblocking local file lock; closing the binary stream releases ownership."""

import errno
import os


def acquire(stream) -> None:
    """Acquire the same first-byte lock on Windows, or flock on POSIX.

    Callers keep their existing bounded wait and pending ownership rules.
    No polling, file replacement or stale-owner takeover is performed here.
    """
    if os.name == "nt":
        import msvcrt

        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise BlockingIOError(error.errno, "Another controller owns the file lock") from error
            raise
    else:
        import fcntl

        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
