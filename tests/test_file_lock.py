"""Use actual OS file descriptors to verify cross-controller exclusion."""

import pytest

from civ_mcp.file_lock import acquire


def test_competing_controller_is_rejected_until_original_handle_closes(tmp_path):
    path = tmp_path / "中文 锁"
    with path.open("a+b") as original, path.open("a+b") as contender:
        acquire(original)
        with pytest.raises(BlockingIOError):
            acquire(contender)
    with path.open("a+b") as successor:
        acquire(successor)
