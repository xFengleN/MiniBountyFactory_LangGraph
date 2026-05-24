import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from src.agents.comment_generator import CommentGenerator


def test_basic_comment():
    gen = CommentGenerator()
    bounty = {'title': 'Fix login bug', 'issue_url': 'https://github.com/a/b/issues/1', 'repository_name': 'a/b'}
    result = gen.generate_intent_comment(bounty, None)
    assert "Hi! I'd like to work on this" in result
    assert "Will open a PR" in result


def test_comment_with_contributing_rules():
    gen = CommentGenerator()
    bounty = {'title': 'Add tests', 'issue_url': 'https://github.com/a/b/issues/2', 'repository_name': 'a/b'}
    check = {'contributing_rules': 'Run tests with pytest'}
    result = gen.generate_intent_comment(bounty, check)
    assert "CONTRIBUTING.md" in result


def test_comment_with_assignment():
    gen = CommentGenerator()
    bounty = {'title': 'Fix bug', 'issue_url': 'https://github.com/a/b/issues/3', 'repository_name': 'a/b'}
    check = {'is_assigned': True, 'assignees': ['user1']}
    result = gen.generate_intent_comment(bounty, check)
    assert "assigned to user1" in result


def test_comment_with_claim():
    gen = CommentGenerator()
    bounty = {'title': 'Fix bug', 'issue_url': 'https://github.com/a/b/issues/3', 'repository_name': 'a/b'}
    check = {'recent_claims': [{'user': 'dev1', 'time': '2h ago'}]}
    result = gen.generate_intent_comment(bounty, check)
    assert "@dev1" in result
    assert "picked this up" in result


def test_comment_with_algora_bot():
    gen = CommentGenerator()
    bounty = {'title': 'Bounty task', 'issue_url': 'https://github.com/a/b/issues/4', 'repository_name': 'a/b'}
    check = {
        'algora_bot_comment': '## $100 bounty\nComment `/attempt #123` with your plan\nInclude `/claim #123` in the PR body',
    }
    result = gen.generate_intent_comment(bounty, check)
    assert "/attempt #123" in result
    assert "/claim #123" in result


def test_comment_without_algora_bot():
    gen = CommentGenerator()
    bounty = {'title': 'Regular issue', 'issue_url': 'https://github.com/a/b/issues/5', 'repository_name': 'a/b'}
    result = gen.generate_intent_comment(bounty, {'algora_bot_comment': None})
    assert "/attempt" not in result


if __name__ == '__main__':
    test_basic_comment()
    test_comment_with_contributing_rules()
    test_comment_with_assignment()
    test_comment_with_claim()
    test_comment_with_algora_bot()
    test_comment_without_algora_bot()
    print("All comment_generator tests passed!")
