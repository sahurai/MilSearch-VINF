# ==============================================================================
# URL FRONTIER TESTS — MilSearch
# ==============================================================================
# PURPOSE:
#     Test the file-based frontier: queue order, resume after a stop and duplicate detection.
#
# RESPONSIBILITIES:
#     1. Make sure that the frontier gives URLs round-robin across categories.
#     2. Make sure that a new frontier continues from the state files.
#     3. Make sure that a URL that a redirect already finished does not come from the queue.
#
# DEPENDENCIES:
#     crawler.frontier — class under test.
# ==============================================================================

from __future__ import annotations

from pathlib import Path

from crawler.frontier import Frontier

# ==============================================================================
# CONSTANTS
# ==============================================================================

_CATEGORIES = ("smallarms", "armor")
_SMALLARMS_1 = "https://www.militaryfactory.com/smallarms/detail.php?smallarms_id=1"
_SMALLARMS_2 = "https://www.militaryfactory.com/smallarms/detail.php?smallarms_id=2"
_SMALLARMS_UNKNOWN = "https://www.militaryfactory.com/smallarms/detail.php?smallarms_id=99999"
_ARMOR_1 = "https://www.militaryfactory.com/armor/detail.php?armor_id=1"


# ==============================================================================
# HELPERS
# ==============================================================================

def _build_frontier(directory: Path) -> Frontier:
    """
    Build a frontier with its state files in a temporary directory.

    Steps:
        1. Load the frontier from discovered.jsonl and fetched.jsonl in the directory.

    Returns:
        Frontier.
    """
    # 1. Load the frontier from discovered.jsonl and fetched.jsonl in the directory
    return Frontier(directory / "discovered.jsonl", directory / "fetched.jsonl", _CATEGORIES)


def _build_done_record(url: str, final_url: str, sha1: str) -> dict[str, object]:
    """
    Build a fetch record of a saved page.

    Steps:
        1. Fill the fields that the frontier reads.

    Returns:
        Record with status "done".
    """
    # 1. Fill the fields that the frontier reads
    return {"url": url, "final_url": final_url, "status": "done", "sha1": sha1}


# ==============================================================================
# MAIN LOGIC
# ==============================================================================

def test_gives_urls_round_robin_across_categories(tmp_path: Path) -> None:
    """
    The frontier alternates between the category queues.

    Steps:
        1. Add two smallarms URLs and one armor URL.
        2. Pop all URLs and check the order.

    Returns:
        None.
    """
    # 1. Add two smallarms URLs and one armor URL
    frontier = _build_frontier(tmp_path)
    frontier.add_url(_SMALLARMS_1, "detail", "smallarms", 0, None)
    frontier.add_url(_SMALLARMS_2, "detail", "smallarms", 0, None)
    frontier.add_url(_ARMOR_1, "detail", "armor", 0, None)

    # 2. Pop all URLs and check the order
    order = [frontier.pop_next_record()["url"] for _ in range(3)]
    assert order == [_SMALLARMS_1, _ARMOR_1, _SMALLARMS_2]
    assert frontier.pop_next_record() is None


def test_new_frontier_continues_from_state_files(tmp_path: Path) -> None:
    """
    After a stop, a new frontier queues only the URLs that are not finished.

    Steps:
        1. Add two URLs and save one as done.
        2. Load a new frontier from the same files.

    Returns:
        None.
    """
    # 1. Add two URLs and save one as done
    frontier = _build_frontier(tmp_path)
    frontier.add_url(_SMALLARMS_1, "detail", "smallarms", 0, None)
    frontier.add_url(_SMALLARMS_2, "detail", "smallarms", 0, None)
    frontier.save_result(_build_done_record(_SMALLARMS_1, _SMALLARMS_1, "hash-1"))

    # 2. Load a new frontier from the same files
    resumed = _build_frontier(tmp_path)
    assert resumed.get_pending_count() == 1
    assert resumed.pop_next_record()["url"] == _SMALLARMS_2
    assert resumed.add_url(_SMALLARMS_1, "detail", "smallarms", 0, None) is False


def test_skips_url_that_a_redirect_already_finished(tmp_path: Path) -> None:
    """
    Regression: an unknown id redirects to id=1, so the queued id=1 must not be downloaded again.

    Steps:
        1. Queue the unknown id and id=1.
        2. Save the unknown id as done with the final URL id=1.
        3. Pop the queue.

    Returns:
        None.
    """
    # 1. Queue the unknown id and id=1
    frontier = _build_frontier(tmp_path)
    frontier.add_url(_SMALLARMS_UNKNOWN, "detail", "smallarms", 0, None)
    frontier.add_url(_SMALLARMS_1, "detail", "smallarms", 0, None)

    # 2. Save the unknown id as done with the final URL id=1
    assert frontier.pop_next_record()["url"] == _SMALLARMS_UNKNOWN
    frontier.save_result(_build_done_record(_SMALLARMS_UNKNOWN, _SMALLARMS_1, "hash-1"))

    # 3. Pop the queue
    assert frontier.pop_next_record() is None
    assert frontier.get_duplicate_url(_SMALLARMS_2, _SMALLARMS_2, "hash-1") == _SMALLARMS_UNKNOWN
