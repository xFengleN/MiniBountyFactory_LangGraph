import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from .database import db
from ..utils.logger import get_logger

logger = get_logger(__name__)

MAX_OBSERVATIONS = 100


class RepoProfiler:
    def __init__(self):
        pass

    def record_observation(self, owner: str, repo: str, check: dict,
                           award_count: int, wip_count: int,
                           algora_entries: list = None,
                           attempt_users: list = None,
                           assignees: list = None) -> dict:
        repo_key = f'{owner}/{repo}'
        now = datetime.now(timezone.utc).isoformat()

        observation = {
            'ts': now,
            'claims': len(check.get('recent_claims', [])),
            'is_assigned': check.get('is_assigned', False),
            'algora_present': bool(check.get('algora_bot_comment')),
            'algora_locked': check.get('algora_status') == 'locked',
            'reward_count': award_count,
            'wip_count': wip_count,
            'has_contributing': check.get('has_contributing', False),
            'pr_count': len(check.get('active_prs', [])),
            'compliance': self._compute_compliance(
                algora_entries or [],
                attempt_users or [],
                assignees or [],
            ),
        }

        existing = db.get_repo_profile(repo_key)
        if existing:
            observations = json.loads(existing['observations'])
        else:
            observations = []

        observations.append(observation)
        if len(observations) > MAX_OBSERVATIONS:
            observations = observations[-MAX_OBSERVATIONS:]

        obs_count = len(observations)
        profile = self._compute_profile(observations)

        db.upsert_repo_profile(
            repo=repo_key,
            owner=owner,
            name=repo,
            observations_json=json.dumps(observations, indent=2),
            profile_json=json.dumps(profile, indent=2),
            obs_count=obs_count,
        )

        logger.debug(f'Repo profile updated for {repo_key}: {obs_count} obs')

        return profile

    def get_profile(self, owner: str, repo: str) -> dict:
        repo_key = f'{owner}/{repo}'
        existing = db.get_repo_profile(repo_key)
        if existing:
            return json.loads(existing['profile_data'])
        return self._default_profile()

    def _compute_compliance(self, algo_entries: list, attempt_users: list, assignees: list) -> dict:
        attempt_set = set(attempt_users)
        assign_set = set(assignees)

        result = {
            'attempted_in_table': 0,
            'assigned_in_table': 0,
            'rewarded_total': 0,
            'rewarded_and_attempted': 0,
            'rewarded_and_assigned': 0,
            'rewarded_with_pr': 0,
        }

        for entry in algo_entries:
            user = entry.get('user', '')
            if user in attempt_set:
                result['attempted_in_table'] += 1
            if user in assign_set:
                result['assigned_in_table'] += 1
            if entry.get('has_reward', False):
                result['rewarded_total'] += 1
                if user in attempt_set:
                    result['rewarded_and_attempted'] += 1
                if user in assign_set:
                    result['rewarded_and_assigned'] += 1
                if entry.get('has_pr', False):
                    result['rewarded_with_pr'] += 1

        return result

    def _compute_profile(self, observations: list) -> dict:
        n = len(observations)
        if n == 0:
            return self._default_profile()

        total_claims = sum(o['claims'] for o in observations)
        assigned_count = sum(1 for o in observations if o['is_assigned'])
        algora_count = sum(1 for o in observations if o['algora_present'])
        locked_count = sum(1 for o in observations if o['algora_locked'])
        total_rewards = sum(o['reward_count'] for o in observations)
        total_wip = sum(o['wip_count'] for o in observations)
        contributing_count = sum(1 for o in observations if o['has_contributing'])
        total_prs = sum(o['pr_count'] for o in observations)

        # Aggregate compliance across all observations
        comp_totals = {
            'attempted_in_table': 0,
            'assigned_in_table': 0,
            'rewarded_total': 0,
            'rewarded_and_attempted': 0,
            'rewarded_and_assigned': 0,
            'rewarded_with_pr': 0,
        }
        for o in observations:
            c = o.get('compliance', {})
            for k in comp_totals:
                comp_totals[k] += c.get(k, 0)

        compliance = {
            'attempt_comment_enforced': round(
                comp_totals['rewarded_and_attempted'] / max(comp_totals['attempted_in_table'], 1), 2),
            'assignment_enforced': round(
                comp_totals['rewarded_and_assigned'] / max(comp_totals['assigned_in_table'], 1), 2),
            'reward_requires_attempt': round(
                comp_totals['rewarded_and_attempted'] / max(comp_totals['rewarded_total'], 1), 2),
            'reward_requires_merge': round(
                comp_totals['rewarded_with_pr'] / max(comp_totals['rewarded_total'], 1), 2),
        }

        return {
            'observation_count': n,
            'avg_claims': round(total_claims / n, 2),
            'assignment_rate': round(assigned_count / n, 2),
            'algora_frequency': round(algora_count / n, 2),
            'algora_locked_rate': round(locked_count / n, 2),
            'avg_rewards': round(total_rewards / n, 2),
            'avg_wip': round(total_wip / n, 2),
            'contributing_rate': round(contributing_count / n, 2),
            'avg_prs': round(total_prs / n, 2),
            'compliance': compliance,
        }

    def _default_profile(self) -> dict:
        return {
            'observation_count': 0,
            'avg_claims': 0.0,
            'assignment_rate': 0.0,
            'algora_frequency': 0.0,
            'algora_locked_rate': 0.0,
            'avg_rewards': 0.0,
            'avg_wip': 0.0,
            'contributing_rate': 0.0,
            'avg_prs': 0.0,
            'compliance': {
                'attempt_comment_enforced': 0.0,
                'assignment_enforced': 0.0,
                'reward_requires_attempt': 0.0,
                'reward_requires_merge': 0.0,
            },
        }


repo_profiler = RepoProfiler()
