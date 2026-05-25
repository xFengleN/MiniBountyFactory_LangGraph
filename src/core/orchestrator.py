import time
import threading
import re
from typing import Dict, Any, Optional, List
from datetime import datetime

from .algora_client import AlgoraClient
from .database import db
from .task_processor import task_processor
from .config import config
from .graph import get_compiled_graph
from .sandbox import kill_running_containers
from ..agents.dispatcher import Dispatcher
from ..agents.simple_coder import SimpleCoder
from ..agents.super_coder import SuperCoder
from ..agents.cicd_specialist import CicdSpecialist
from ..agents.pr_creator import PRCreator
from ..agents.github_scout import GitHubScout
from ..agents.github_checker import GitHubIssueChecker
from ..agents.comment_generator import CommentGenerator
from ..agents.repo_mapper import RepoMapper
from .repo_profiler import repo_profiler
from ..utils.logger import get_logger

logger = get_logger(__name__)


class BountyFactoryOrchestrator:
    def __init__(self):
        self.algora_client = AlgoraClient()
        self.github_scout = GitHubScout(config.git.get('token'))
        self.github_checker = GitHubIssueChecker(config.git.get('token'))
        self.comment_generator = CommentGenerator()
        self.dispatcher = Dispatcher()
        self.simple_coder = SimpleCoder()
        self.super_coder = SuperCoder()
        self.cicd_specialist = CicdSpecialist()
        self.pr_creator = PRCreator()
        self.repo_mapper = RepoMapper()

        self.running = False
        self.worker_thread = None
        self.fetch_interval = config.get('agents.fetch_interval', 600)
        self.start_config = {
            'mode': 'free',
            'min_price': 0,
            'max_price': 0,
            'scan_interval': 600,
        }

    def get_start_config(self) -> Dict[str, Any]:
        return dict(self.start_config)

    def start(self, **kwargs):
        logger.info("Starting Bounty Factory Orchestrator")
        self.start_config['mode'] = kwargs.get('mode', 'free')
        self.start_config['min_price'] = int(kwargs.get('min_price', 0))
        self.start_config['max_price'] = int(kwargs.get('max_price', 0))
        self.start_config['scan_interval'] = int(kwargs.get('scan_interval', 600))
        self.fetch_interval = self.start_config['scan_interval']
        self.running = True
        max_concurrent = config.get('agents.max_concurrent_tasks', 1)
        task_processor.start(max_concurrent=max_concurrent)

        self.worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self.worker_thread.start()

        logger.info(f"Bounty Factory started (mode={self.start_config['mode']}, price=${self.start_config['min_price']}-${self.start_config['max_price']}, interval={self.fetch_interval}s)")

    def stop(self):
        if not self.running:
            return
        logger.info("Stopping Bounty Factory Orchestrator")
        self.running = False

        task_processor.stop(timeout=3)

        killed = kill_running_containers()
        if killed > 0:
            logger.info(f"Killed {killed} running sandbox containers")

        reset = db.reset_processing_bounties()
        if reset > 0:
            logger.info(f"Reset {reset} interrupted bounties back to 'new'")

        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=3)
            if self.worker_thread.is_alive():
                logger.warning("Worker thread did not finish within timeout")

    def _run_worker(self):
        while self.running:
            try:
                self._process_cycle()
            except Exception as e:
                logger.error(f"Worker cycle failed: {e}")

            for _ in range(self.fetch_interval):
                if not self.running:
                    break
                time.sleep(1)

    def _process_cycle(self):
        logger.info("Starting processing cycle")
        cfg = self.start_config

        db.cleanup_stale_tasks(days=30)
        db.cleanup_old_logs(days=30)

        if cfg['mode'] in ('paid', 'both'):
            logger.info("Fetching paid bounties from Algora...")
            algora_bounties = self.algora_client.fetch_bounties(limit=50)
            min_p = cfg['min_price']
            max_p = cfg['max_price']
            if min_p > 0 or max_p > 0:
                filtered = []
                for b in algora_bounties:
                    price = b.get('price')
                    if price is None:
                        continue
                    if min_p > 0 and price < min_p:
                        continue
                    if max_p > 0 and price > max_p:
                        continue
                    filtered.append(b)
                algora_bounties = filtered
            if algora_bounties:
                self.github_scout.store_issues(algora_bounties)
                logger.info(f"Stored {len(algora_bounties)} paid bounties")

        if cfg['mode'] in ('free', 'both'):
            if self.github_scout.is_available():
                logger.info("Fetching free issues from GitHub...")
                gh_issues = self.github_scout.search_issues(limit=20)
                bp_min = cfg.get('min_price', 0)
                bp_max = cfg.get('max_price', 0)
                if bp_min > 0 or bp_max > 0:
                    gh_issues = [i for i in gh_issues if i.get('price') is not None and bp_min <= i['price'] <= bp_max]
                    logger.info(f"Background price filter ${bp_min}-${bp_max}: {len(gh_issues)} issues kept")
                elif config.get('test_mode.skip_paid', False):
                    before = len(gh_issues)
                    gh_issues = [i for i in gh_issues if not i.get('is_bounty') and not i.get('price')]
                    if before != len(gh_issues):
                        logger.info(f"skip_paid: filtered {before - len(gh_issues)} paid issues (kept {len(gh_issues)})")
                if gh_issues:
                    self.github_scout.store_issues(gh_issues)
                    logger.info(f"Stored {len(gh_issues)} free issues")

        pending_bounties = db.get_pending_bounties()
        min_p = cfg['min_price']
        max_p = cfg['max_price']
        if min_p > 0 or max_p > 0:
            pending_bounties = [
                b for b in pending_bounties
                if b.get('price') is not None
                and (min_p <= 0 or b['price'] >= min_p)
                and (max_p <= 0 or b['price'] <= max_p)
            ]
        logger.info(f"Processing {len(pending_bounties)} pending bounties")

        for bounty in pending_bounties:
            if not self.running:
                break
            self._process_bounty(bounty)

    def _process_bounty(self, bounty: Dict[str, Any]) -> Dict[str, Any]:
        bounty_id = bounty['id']
        title = bounty['title']

        logger.info(f"Processing bounty {bounty_id}: {title}")

        db.update_bounty_status(bounty_id, 'processing')
        db.log_processing(bounty_id, 'orchestrator', 'start', 'processing')

        try:
            graph = get_compiled_graph()
            config_data = {"configurable": {"thread_id": str(bounty_id)}}

            initial_state = {
                "bounty_id": bounty_id,
                "bounty": bounty,
                "retry_count": 0,
            }

            final_state = graph.invoke(initial_state, config=config_data)

            if final_state.get("should_skip"):
                reason = final_state.get("skip_reason", "No reason")
                logger.info(f"Bounty {bounty_id} skipped: {reason}")
                db.update_bounty_status(bounty_id, 'skipped')
                db.log_processing(bounty_id, 'orchestrator', f'skipped: {reason}', 'warning')
                return {'success': False, 'skip': True, 'skip_reason': reason}

            status = final_state.get("status", "")
            if status == "queued_for_review":
                logger.info(f"Bounty {bounty_id} queued for review via graph")
                return {
                    'success': True,
                    'model_used': final_state.get('model_used', ''),
                    'token_stats': final_state.get('token_stats', {}),
                    'duration': final_state.get('duration', 0),
                }
            elif status == "failed":
                error = final_state.get("error")
                if not error:
                    last_errors = final_state.get("last_validation_errors", [])
                    if last_errors:
                        error = f"Validation failed after retries exhausted: {'; '.join(last_errors[:3])}"
                    else:
                        error = "Unknown error"
                logger.warning(f"Bounty {bounty_id} failed in graph: {error}")
                db.update_bounty_status(bounty_id, 'failed')
                return {'success': False, 'error': error}
            else:
                db.update_bounty_status(bounty_id, 'error')
                return {'success': False, 'error': f'Unexpected status: {status}'}

        except Exception as e:
            logger.error(f"Failed to process bounty {bounty_id}: {e}")
            db.update_bounty_status(bounty_id, 'error')
            db.log_processing(bounty_id, 'orchestrator', 'error', 'error', str(e))
            return {'success': False, 'error': str(e)}

    def submit_pr(self, review_id: int) -> Optional[str]:
        reviews = db.get_pending_reviews()
        review = next((r for r in reviews if r['id'] == review_id), None)

        if not review:
            logger.error(f"Review {review_id} not found")
            return None

        bounty = db.get_bounty_by_id(review['bounty_id'])
        if not bounty:
            logger.error(f"Bounty not found for review {review_id}")
            return None

        pr_url = self.pr_creator.create_pr(
            repo_url=bounty['repository_url'],
            branch_name=review['branch_name'],
            bounty=bounty,
            commit_sha=review['commit_sha'],
            workspace_path=review.get('workspace_path'),
        )

        if pr_url:
            db.update_review_pr(review_id, pr_url)
            db.update_bounty_status(bounty['id'], 'pr_created')
            logger.info(f"PR submitted for bounty {bounty['id']}: {pr_url}")

        return pr_url

    def get_status(self) -> Dict[str, Any]:
        return {
            'running': self.running,
            'dispatcher_available': self.dispatcher.is_available(),
            'simple_coder_available': self.simple_coder.is_available(),
            'super_coder_available': self.super_coder.is_available(),
            'cicd_specialist_available': self.cicd_specialist.is_available(),
            'pr_creator_configured': self.pr_creator.is_configured(),
            'github_scout_available': self.github_scout.is_available(),
            'repo_mapper_available': True,
            'pending_reviews': len(db.get_pending_reviews())
        }

    def pre_check_bounty(self, bounty_id: int) -> Dict[str, Any]:
        bounty = db.get_bounty_by_id(bounty_id)
        if not bounty:
            return {'error': 'Bounty not found'}

        issue_url = bounty.get('issue_url', '')
        if not issue_url:
            return {'valid': False, 'error': 'No issue URL available'}

        check_result = self.github_checker.check_issue(issue_url)
        suggested_comment = self.comment_generator.generate_intent_comment(bounty, check_result)

        algora_status = check_result.get('algora_status')
        active_prs = check_result.get('active_prs', [])
        winning_prs = [p for p in active_prs if p.get('ci_passing')]

        return {
            'valid': check_result.get('valid', False),
            'is_assigned': check_result.get('is_assigned', False),
            'assignees': check_result.get('assignees', []),
            'recent_claims': check_result.get('recent_claims', []),
            'has_contributing': check_result.get('has_contributing', False),
            'contributing_rules': check_result.get('contributing_rules', ''),
            'algora_status': algora_status,
            'algora_assignee': check_result.get('algora_assignee'),
            'algora_bot_comment': check_result.get('algora_bot_comment'),
            'active_prs': active_prs,
            'winning_prs': winning_prs,
            'warnings': check_result.get('warnings', []),
            'suggested_comment': suggested_comment,
        }

    def post_comment(self, bounty_id: int, body: str) -> Dict[str, Any]:
        bounty = db.get_bounty_by_id(bounty_id)
        if not bounty:
            return {'success': False, 'error': 'Bounty not found'}
        issue_url = bounty.get('issue_url', '')
        if not issue_url:
            return {'success': False, 'error': 'No issue URL'}
        ok = self.github_checker.post_comment(issue_url, body)
        if ok:
            db.log_processing(bounty_id, 'comment', 'Comment posted to GitHub', 'info')
            return {'success': True}
        db.log_processing(bounty_id, 'comment', 'Failed to post comment', 'error')
        return {'success': False, 'error': 'Failed to post comment to GitHub'}

    def plan_attempt(self, bounty_id: int) -> Dict[str, Any]:
        bounty = db.get_bounty_by_id(bounty_id)
        if not bounty:
            return {'success': False, 'error': 'Bounty not found'}

        issue_url = bounty.get('issue_url', '')
        if not issue_url:
            return {'success': False, 'error': 'No issue URL'}

        db.update_bounty_status(bounty_id, 'planning')
        db.log_processing(bounty_id, 'plan_attempt', 'start', 'planning')

        try:
            check = self.github_checker.check_issue(issue_url)
            if not check.get('valid'):
                return {'success': False, 'error': check.get('error', 'Pre-check failed')}

            number = check['number']
            title = bounty.get('title', '')
            description = bounty.get('description', '')
            repo_url = bounty.get('repository_url', '')
            bot = check.get('algora_bot_comment', '')

            plan_comment = self.comment_generator.generate_attempt_comment(
                issue_number=number, title=title, description=description,
                repo_url=repo_url, bot_comment=bot,
                check_result=check,
            )
            comment_ok = self.github_checker.post_comment(issue_url, plan_comment)

            if not comment_ok:
                db.update_bounty_status(bounty_id, 'new')
                return {'success': False, 'error': 'Failed to post /attempt comment'}

            db.update_bounty_status(bounty_id, 'awaiting_assignment')
            db.log_processing(bounty_id, 'plan_attempt', f'Posted /attempt #{number}', 'awaiting_assignment')

            return {
                'success': True,
                'issue_number': number,
                'plan': plan_comment,
                'status': 'awaiting_assignment',
            }
        except Exception as e:
            logger.error(f"Plan attempt failed for bounty {bounty_id}: {e}")
            db.update_bounty_status(bounty_id, 'new')
            return {'success': False, 'error': str(e)}

    def plan_attempt_preview(self, bounty_id: int) -> Dict[str, Any]:
        bounty = db.get_bounty_by_id(bounty_id)
        if not bounty:
            return {'error': 'Bounty not found'}
        issue_url = bounty.get('issue_url', '')
        if not issue_url:
            return {'error': 'No issue URL available'}
        check = self.github_checker.check_issue(issue_url, force=True)
        if not check.get('valid'):
            return {'error': check.get('error', 'Failed to fetch issue')}
        active_prs = check.get('active_prs', [])
        winning_prs = [p for p in active_prs if p.get('ci_passing')]
        precheck = {
            'valid': check.get('valid', False),
            'is_assigned': check.get('is_assigned', False),
            'assignees': check.get('assignees', []),
            'recent_claims': check.get('recent_claims', []),
            'has_contributing': check.get('has_contributing', False),
            'contributing_rules': check.get('contributing_rules', ''),
            'algora_status': check.get('algora_status'),
            'algora_assignee': check.get('algora_assignee'),
            'algora_bot_comment': check.get('algora_bot_comment'),
            'active_prs': active_prs,
            'winning_prs': winning_prs,
            'warnings': check.get('warnings', []),
            'suggested_comment': self.comment_generator.generate_intent_comment(bounty, check),
        }
        precheck['generated_comment'] = self.comment_generator.generate_attempt_comment(
            issue_number=check['number'],
            title=bounty.get('title', ''),
            description=bounty.get('description', ''),
            repo_url=bounty.get('repository_url', ''),
            bot_comment=check.get('algora_bot_comment', ''),
            check_result=check,
        )
        bot = check.get('algora_bot_comment', '') or ''
        wip_count = 0
        award_total = 0
        award_count = 0
        algora_entries = []
        wip_markers = ['☑', '✅', '🟡', '⏳', 'yes', 'wip', 'in progress']

        attempt_users = [c.get('user', '') for c in check.get('recent_claims', [])]
        assignees = check.get('assignees', [])

        # Count $amounts from ALL lines for the monetary total
        for d in re.findall(r'\$(\d+(?:,\d{3})*(?:\.\d{2})?)', bot):
            award_total += int(d.replace(',', ''))

        # Parse table data rows for award/wip counts + entry-level data
        for line in bot.split('\n'):
            stripped = line.strip()
            if not stripped.startswith('|') or not stripped.endswith('|'):
                continue
            if re.match(r'^\|[\s\-:]+\|', stripped):
                continue
            cells = [c.strip() for c in stripped.split('|') if c.strip()]
            if len(cells) < 2:
                continue
            if cells[0].lower() in ('attempt', 'wip', 'issue', '#'):
                continue

            has_reward = 'Reward' in stripped and '/claims/' in stripped
            has_wip = any(m in stripped.lower() for m in wip_markers)
            has_dollar = '$' in stripped

            if has_reward:
                award_count += 1
            elif has_dollar:
                award_count += 1
                if has_wip:
                    wip_count += 1
            elif has_wip:
                wip_count += 1

            # Extract user and PR for protocol compliance
            entry_user = ''
            entry_pr = None
            for cell in cells:
                m = re.search(r'@(\w[\w-]*)', cell)
                if m:
                    entry_user = m.group(1)
                m = re.search(r'#(\d+)', cell)
                if m:
                    entry_pr = m.group(1)

            algora_entries.append({
                'user': entry_user,
                'has_reward': has_reward or has_dollar,
                'has_pr': entry_pr is not None,
            })

        precheck['algora_wip_count'] = wip_count
        precheck['algora_award_count'] = award_count
        precheck['algora_award_total'] = award_total
        precheck['generated_by'] = 'Bounty Factory — template-based planner (no LLM used for plan generation)'
        precheck['_debug_raw_bot'] = bot[:2000]

        owner = check.get('owner', '')
        repo_name = check.get('repo', '')
        if owner and repo_name:
            repo_profiler.record_observation(
                owner, repo_name, check, award_count, wip_count,
                algora_entries=algora_entries,
                attempt_users=attempt_users,
                assignees=assignees,
            )
            precheck['repo_profile'] = repo_profiler.get_profile(owner, repo_name)
            precheck['ripeness'] = repo_profiler.compute_ripeness(check, precheck['repo_profile'])
        else:
            precheck['repo_profile'] = repo_profiler._default_profile()
            precheck['ripeness'] = repo_profiler.compute_ripeness(check, precheck['repo_profile'])

        logger.debug(f'Bounty {bounty_id}: Algora parser — wip={wip_count}, awards={award_count}, total=${award_total}')
        return precheck

    def submit_attempt_comment(self, bounty_id: int, body: str = '', execute: bool = False) -> Dict[str, Any]:
        bounty = db.get_bounty_by_id(bounty_id)
        if not bounty:
            return {'success': False, 'error': 'Bounty not found'}
        if execute:
            db.update_bounty_status(bounty_id, 'processing')
            db.log_processing(bounty_id, 'submit_attempt', 'Started execution (manual test, no comment posted)', 'processing')
            return self.process_single_bounty(bounty_id)
        issue_url = bounty.get('issue_url', '')
        if not issue_url:
            return {'success': False, 'error': 'No issue URL'}
        if not body:
            return {'success': False, 'error': 'Comment body is required for Send'}
        comment_ok = self.github_checker.post_comment(issue_url, body)
        if not comment_ok:
            return {'success': False, 'error': 'Failed to post comment'}
        db.update_bounty_status(bounty_id, 'awaiting_assignment')
        db.log_processing(bounty_id, 'submit_attempt', 'Posted /attempt comment, awaiting assignment', 'awaiting_assignment')
        return {'success': True, 'status': 'awaiting_assignment'}

    def execute_bounty(self, bounty_id: int) -> Dict[str, Any]:
        return self.process_single_bounty(bounty_id)

    def process_single_bounty(self, bounty_id: int) -> Dict[str, Any]:
        bounty = db.get_bounty_by_id(bounty_id)
        if not bounty:
            return {'success': False, 'error': 'Bounty not found'}

        db.update_bounty_status(bounty_id, 'processing')
        db.log_processing(bounty_id, 'orchestrator', 'submitted', 'processing', 'Task queued for processing')

        task_processor.submit(bounty_id, self._process_bounty_sync)
        return {'success': True, 'bounty_id': bounty_id, 'queued': True}

    def _process_bounty_sync(self, bounty_id: int) -> Dict[str, Any]:
        bounty = db.get_bounty_by_id(bounty_id)
        if not bounty:
            return {'success': False, 'error': 'Bounty not found'}

        result = self._process_bounty(bounty)
        return {
            'success': result.get('success', False),
            'bounty_id': bounty_id,
            'model_used': result.get('model_used', ''),
            'token_stats': result.get('token_stats', {}),
            'duration': result.get('duration', 0),
        }

    def manual_scan(
        self,
        test_mode: bool = True,
        labels: list = None,
        limit: int = 10,
        min_price: int = 0,
        max_price: int = 0
    ) -> dict:
        mode = 'test' if test_mode else 'prod'
        logger.info(f"Manual scan: mode={mode}, labels={labels}, limit={limit}, price=${min_price}-${max_price}")

        db.cleanup_stale_tasks(days=30)

        new_count = 0

        if test_mode:
            if self.github_scout.is_available():
                if labels:
                    queries = [f'label:"{label}" state:open' for label in labels]
                else:
                    queries = config.get('test_mode.github_queries', [])[:]

                wants_paid = min_price > 0 or max_price > 0
                if wants_paid:
                    bounty_q = 'label:"bounty" state:open'
                    if bounty_q not in queries:
                        queries.append(bounty_q)

                issues = []
                per_query = max(1, limit // len(queries)) if queries else limit
                for q in queries:
                    issues.extend(self.github_scout.search_issues(query=q, limit=per_query))
                    if len(issues) >= limit:
                        break
                issues = issues[:limit]

                if wants_paid:
                    before = len(issues)
                    def in_range(i):
                        p = i.get('price')
                        return p is not None and min_price <= p <= max_price
                    issues = [i for i in issues if in_range(i)]
                    if before != len(issues):
                        logger.info("price filter $%s-$%s: filtered %d issues (kept %d)", min_price, max_price, before - len(issues), len(issues))
                elif config.get('test_mode.skip_paid', False):
                    before = len(issues)
                    issues = [i for i in issues if not i.get('is_bounty') and not i.get('price')]
                    if before != len(issues):
                        logger.info(f"skip_paid: filtered {before - len(issues)} paid issues (kept {len(issues)})")

                new_count = self.github_scout.store_issues(issues)

                if wants_paid:
                    try:
                        algo = self.algora_client.fetch_bounties(limit=limit)
                        filt = [b for b in algo if b.get('price') and min_price <= b['price'] <= max_price]
                        if filt:
                            added = self.github_scout.store_issues(filt)
                            if added:
                                new_count += added
                                logger.info(f"Algora fallback: stored {added} new paid bounties")
                    except Exception as e:
                        logger.warning(f"Algora fallback failed: {e}")
        else:
            algora_bounties = self.algora_client.fetch_bounties(limit=limit)

            if min_price > 0 or max_price > 0:
                filtered = []
                for bounty in algora_bounties:
                    price = bounty.get('price')
                    if price is None:
                        continue
                    if min_price > 0 and price < min_price:
                        continue
                    if max_price > 0 and price > max_price:
                        continue
                    filtered.append(bounty)
                algora_bounties = filtered

            new_count = self.github_scout.store_issues(algora_bounties)

            if self.github_scout.is_available():
                bounty_queries = [
                    'label:"bounty" state:open',
                    'label:"bug bounty" state:open',
                    'label:"reward" state:open',
                ]
                gh_issues = []
                per_query = max(1, limit // len(bounty_queries))
                for q in bounty_queries:
                    gh_issues.extend(self.github_scout.search_issues(query=q, limit=per_query))
                    if len(gh_issues) >= limit:
                        break
                gh_issues = gh_issues[:limit]

                if min_price > 0 or max_price > 0:
                    filtered = []
                    for issue in gh_issues:
                        price = issue.get('price')
                        if price is None:
                            continue
                        if min_price > 0 and price < min_price:
                            continue
                        if max_price > 0 and price > max_price:
                            continue
                        filtered.append(issue)
                    gh_issues = filtered

                gh_count = self.github_scout.store_issues(gh_issues)
                new_count += gh_count

        total_match = 0
        if min_price > 0 or max_price > 0:
            all_bounties = db.get_all_bounties()
            total_match = sum(1 for b in all_bounties if b.get('price') and min_price <= b['price'] <= max_price)

        logger.info(f"Manual scan: {new_count} new, {total_match} total matching in DB")
        return {'new': new_count, 'total': total_match}
