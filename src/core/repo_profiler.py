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

    def compute_ripeness(self, check: dict, profile: dict) -> dict:
        created_str = check.get('issue_created_at')
        score = 50
        factors = []
        hours = None

        if created_str:
            try:
                created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                hours = (now - created).total_seconds() / 3600
            except Exception:
                pass

        if hours is not None:
                if hours < 24:
                    score -= 20
                    factors.append('fresh_issue_under_24h')
                elif hours > 168:
                    score += 10
                    factors.append('stale_over_7d')

        claim_count = len(check.get('recent_claims', []))
        if claim_count >= 5:
            score -= 15
            factors.append(f'high_competition_{claim_count}_claims')
        elif claim_count >= 2:
            score -= 5
            factors.append(f'some_competition_{claim_count}_claims')
        elif claim_count == 0:
            score += 5
            factors.append('no_competition')

        if check.get('is_assigned'):
            if hours is not None and hours > 120:
                score += 15
                factors.append('assigned_inactive_5d_plus')
            else:
                score -= 15
                factors.append('recently_assigned')
        else:
            score += 10
            factors.append('unassigned')

        if check.get('algora_status') == 'locked':
            score -= 20
            factors.append('algora_locked_exclusive')

        non_ci_prs = [p for p in check.get('active_prs', []) if not p.get('ci_passing')]
        failed = len(non_ci_prs)
        if failed >= 3 and (hours is None or hours > 72):
            score += 20
            factors.append(f'{failed}_failed_prs_opportunity')
        elif failed >= 1:
            score += 5
            factors.append(f'{failed}_previous_attempts')

        obs_count = profile.get('observation_count', 0)
        if obs_count > 0:
            avg_claims = profile.get('avg_claims', 0)
            if claim_count > avg_claims * 1.5:
                score -= 5
                factors.append('above_repo_avg_competition')

        clamped = max(0, min(100, score))
        return {
            'score': clamped,
            'label': 'high' if clamped >= 65 else 'medium' if clamped >= 35 else 'low',
            'factors': factors,
            'confidence': 'high' if obs_count > 10 else 'medium' if obs_count > 3 else 'low',
        }

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
