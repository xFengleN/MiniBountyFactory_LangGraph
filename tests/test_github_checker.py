import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from src.agents.github_checker import GitHubIssueChecker


def test_parse_issue_url():
    checker = GitHubIssueChecker()
    owner, repo, number = checker._parse_issue_url('https://github.com/owner/repo/issues/42')
    assert owner == 'owner'
    assert repo == 'repo'
    assert number == 42


def test_parse_issue_url_invalid():
    checker = GitHubIssueChecker()
    owner, repo, number = checker._parse_issue_url('not-a-url')
    assert owner is None


def test_parse_issue_url_with_trailing_slash():
    checker = GitHubIssueChecker()
    owner, repo, number = checker._parse_issue_url('https://github.com/a/b/issues/123/')
    assert owner == 'a'
    assert repo == 'b'
    assert number == 123


def test_detect_claims():
    checker = GitHubIssueChecker()
    comments = [
        {'user': {'login': 'dev1'}, 'body': "I'll work on this", 'created_at': '2026-05-01T00:00:00Z'},
        {'user': {'login': 'dev2'}, 'body': '/attempt', 'created_at': '2026-05-02T00:00:00Z'},
    ]
    claims = checker._detect_claims(comments)
    assert len(claims) == 2


def test_detect_claims_skips_bots():
    checker = GitHubIssueChecker()
    comments = [
        {'user': {'login': 'algora-pbc[bot]'}, 'body': '/attempt #123', 'created_at': '2026-05-01T00:00:00Z'},
        {'user': {'login': 'github-actions[bot]'}, 'body': '/claim', 'created_at': '2026-05-01T00:00:00Z'},
    ]
    claims = checker._detect_claims(comments)
    assert len(claims) == 0


def test_detect_claims_empty():
    checker = GitHubIssueChecker()
    claims = checker._detect_claims([])
    assert len(claims) == 0


def test_check_algora_exclusivity_locked():
    checker = GitHubIssueChecker()
    comments = [
        {'user': {'login': 'algora-pbc[bot]'}, 'body': 'Bounty assigned to @dev1', 'created_at': '2026-05-01T00:00:00Z'},
    ]
    result = checker._check_algora_exclusivity(comments)
    assert result['status'] == 'locked'
    assert result['assignee'] == 'dev1'


def test_check_algora_exclusivity_released():
    checker = GitHubIssueChecker()
    # comments in API order (newest first) so reversed() gives oldest-first chronological
    comments = [
        {'user': {'login': 'algora-pbc[bot]'}, 'body': 'Bounty unassigned', 'created_at': '2026-05-02T00:00:00Z'},
        {'user': {'login': 'algora-pbc[bot]'}, 'body': 'Bounty assigned to @dev1', 'created_at': '2026-05-01T00:00:00Z'},
    ]
    result = checker._check_algora_exclusivity(comments)
    assert result['status'] == 'open'


def test_find_prs_in_comments():
    checker = GitHubIssueChecker()
    comments = [
        {'user': {'login': 'dev1'}, 'body': 'See PR #456 for details', 'created_at': '2026-05-01T00:00:00Z'},
    ]
    prs = checker._find_prs_in_comments(comments)
    assert len(prs) == 1
    assert prs[0]['number'] == 456


def test_cache_hit():
    checker = GitHubIssueChecker()
    checker._cache_set('test_key', 'test_val')
    result = checker._cache_get('test_key')
    assert result == 'test_val'


def test_cache_miss():
    checker = GitHubIssueChecker()
    result = checker._cache_get('nonexistent')
    assert result is None


def test_cache_expiry():
    checker = GitHubIssueChecker()
    checker._cache_ttl = 0
    checker._cache_set('test_key', 'test_val')
    import time
    time.sleep(0.01)
    result = checker._cache_get('test_key')
    assert result is None


if __name__ == '__main__':
    test_parse_issue_url()
    test_parse_issue_url_invalid()
    test_parse_issue_url_with_trailing_slash()
    test_detect_claims()
    test_detect_claims_skips_bots()
    test_detect_claims_empty()
    test_check_algora_exclusivity_locked()
    test_check_algora_exclusivity_released()
    test_find_prs_in_comments()
    test_cache_hit()
    test_cache_miss()
    test_cache_expiry()
    print("All github_checker tests passed!")
