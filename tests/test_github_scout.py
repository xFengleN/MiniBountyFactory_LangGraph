import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from src.agents.github_scout import GitHubScout


def test_extract_price_dollar():
    scout = GitHubScout()
    assert scout._extract_price('Fix bug $500', '') == 500


def test_extract_price_usd():
    scout = GitHubScout()
    assert scout._extract_price('Reward 250 USD', '') == 250


def test_extract_price_bounty_prefix():
    scout = GitHubScout()
    assert scout._extract_price('Bounty: $1000', '') == 1000


def test_extract_price_reward_prefix():
    scout = GitHubScout()
    assert scout._extract_price('Reward: $750', '') == 750


def test_extract_price_k_suffix():
    scout = GitHubScout()
    assert scout._extract_price('$3.5k Bounty', '') == 3500


def test_extract_price_uppercase_k():
    scout = GitHubScout()
    assert scout._extract_price('$2K reward', '') == 2000


def test_extract_price_no_match():
    scout = GitHubScout()
    assert scout._extract_price('No price here', '') is None


def test_extract_price_from_body():
    scout = GitHubScout()
    assert scout._extract_price('Title', 'Bounty: $1500') == 1500


def test_extract_price_currency_bracket():
    scout = GitHubScout()
    assert scout._extract_price('[500 USD]', '') == 500


def test_calculate_score_bug():
    scout = GitHubScout()
    score = scout._calculate_score({'labels': [{'name': 'bug'}], 'title': 'fix crash'})
    assert score == 5  # bug (3) + fix (2)


def test_calculate_score_good_first_issue():
    scout = GitHubScout()
    score = scout._calculate_score({'labels': [{'name': 'good first issue'}], 'title': 'improve docs'})
    assert score >= 5  # good first issue (5), docs/typo not matched


def test_estimate_difficulty_typo():
    scout = GitHubScout()
    assert scout._estimate_difficulty({'labels': [{'name': 'good first issue'}], 'title': 'fix a typo'}) == 'easy-1'


def test_estimate_difficulty_medium():
    scout = GitHubScout()
    diff = scout._estimate_difficulty({'labels': [{'name': 'help wanted'}], 'title': 'refactor module'})
    assert 'medium' in diff


if __name__ == '__main__':
    test_extract_price_dollar()
    test_extract_price_usd()
    test_extract_price_bounty_prefix()
    test_extract_price_reward_prefix()
    test_extract_price_k_suffix()
    test_extract_price_uppercase_k()
    test_extract_price_no_match()
    test_extract_price_from_body()
    test_extract_price_currency_bracket()
    test_calculate_score_bug()
    test_calculate_score_good_first_issue()
    test_estimate_difficulty_typo()
    test_estimate_difficulty_medium()
    print("All github_scout tests passed!")
