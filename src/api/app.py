import os
import time
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pathlib import Path

from ..core.orchestrator import BountyFactoryOrchestrator
from ..core.database import db
from ..core.task_processor import task_processor
from ..utils.logger import get_logger

logger = get_logger(__name__)

app = Flask(__name__)
CORS(app)

orchestrator = None
_startup_time = None

WEB_UI_PATH = Path(__file__).parent / 'web_ui.html'


@app.route('/')
def serve_web_ui():
    if WEB_UI_PATH.exists():
        return send_file(WEB_UI_PATH)
    return '''
    <html>
    <head><title>Bounty Factory</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; }
        .status-dot.green { background-color: #22c55e; }
        .status-dot.red { background-color: #ef4444; }
        .status-dot.yellow { background-color: #eab308; }
        .tab-active { border-bottom: 2px solid #a855f7; color: #a855f7; }
    </style>
    </head>
    <body class="bg-gray-900 text-white">
        <div class="min-h-screen">
            <nav class="bg-gray-800 border-b border-gray-700">
                <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div class="flex items-center justify-between h-16">
                        <div class="flex items-center">
                            <i class="fas fa-robot text-2xl text-purple-500 mr-3"></i>
                            <span class="text-xl font-bold">Bounty Factory</span>
                        </div>
                        <div class="flex items-center space-x-3">
                            <button onclick="openScanModal()" id="scanBtn" class="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded">
                                <i class="fas fa-search mr-1"></i> Scan Tasks
                            </button>
                            <button onclick="startSystem()" class="bg-green-600 hover:bg-green-700 px-4 py-2 rounded">Start</button>
                            <button onclick="stopSystem()" class="bg-red-600 hover:bg-red-700 px-4 py-2 rounded">Stop</button>
                            <button onclick="openSettings()" class="bg-gray-600 hover:bg-gray-700 px-3 py-2 rounded" title="Settings">
                                <i class="fas fa-cog"></i>
                            </button>
                            <button onclick="refreshAll()" class="bg-gray-600 hover:bg-gray-700 px-3 py-2 rounded">
                                <i class="fas fa-sync"></i>
                            </button>
                        </div>
                    </div>
                </div>
            </nav>

            <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
                <div class="grid grid-cols-1 md:grid-cols-6 gap-4 mb-6">
                    <div class="bg-gray-800 rounded-lg p-4">
                        <div class="text-gray-400 text-sm">System Status</div>
                        <div class="flex items-center mt-2">
                            <span id="systemStatus" class="status-dot yellow"></span>
                            <span id="systemStatusText" class="ml-2">Loading...</span>
                        </div>
                        <div class="mt-2 text-xs text-gray-500">Uptime: <span id="uptimeText">-</span></div>
                    </div>
                    <div class="bg-gray-800 rounded-lg p-4">
                        <div class="text-gray-400 text-sm">Ollama</div>
                        <div class="mt-1 space-y-0.5 text-sm">
                            <div id="ollamaStatus">Loading...</div>
                            <div id="ollamaModel" class="text-xs text-gray-500"></div>
                        </div>
                    </div>
                    <div class="bg-gray-800 rounded-lg p-4">
                        <div class="text-gray-400 text-sm">Sandbox</div>
                        <div class="mt-1 space-y-0.5 text-sm">
                            <div id="sandboxStatus">Loading...</div>
                            <div id="sandboxImage" class="text-xs text-gray-500"></div>
                        </div>
                    </div>
                    <div class="bg-gray-800 rounded-lg p-4">
                        <div class="text-gray-400 text-sm">Today's Stats</div>
                        <div class="mt-1 space-y-0.5 text-sm">
                            <div id="todayProcessed">Processed: -</div>
                            <div id="todaySuccess">Success: -</div>
                            <div id="todayAvgTime">Avg time: -</div>
                        </div>
                    </div>
                    <div class="bg-gray-800 rounded-lg p-4">
                        <div class="text-gray-400 text-sm">Database</div>
                        <div class="mt-1 space-y-0.5 text-sm">
                            <div id="dbTotalBounties">Bounties: -</div>
                            <div id="dbTotalReviews">Reviews: -</div>
                            <div id="dbSize" class="text-xs text-gray-500"></div>
                        </div>
                    </div>
                    <div class="bg-gray-800 rounded-lg p-4">
                        <div class="text-gray-400 text-sm">Quick View</div>
                        <div class="mt-1 space-y-0.5 text-sm">
                            <div id="qvNew" class="cursor-pointer hover:text-blue-400" onclick="switchTab('new')">New: <span class="font-bold text-blue-400">-</span></div>
                            <div id="qvFailed" class="cursor-pointer hover:text-red-400" onclick="switchTab('failed')">Failed: <span class="font-bold text-red-400">-</span></div>
                            <div id="qvReview" class="cursor-pointer hover:text-purple-400" onclick="switchTab('queued_for_review')">Review: <span class="font-bold text-purple-400">-</span></div>
                        </div>
                    </div>
                </div>

                <div class="bg-gray-800 rounded-lg mb-6">
                    <div class="flex border-b border-gray-700">
                        <button onclick="switchTab('new')" id="tab-new" class="tab-active px-6 py-3 font-medium">
                            <i class="fas fa-inbox mr-2"></i>New <span id="count-new" class="ml-1 text-xs bg-blue-600 px-2 py-0.5 rounded-full">0</span>
                        </button>
                        <button onclick="switchTab('processing')" id="tab-processing" class="px-6 py-3 font-medium text-gray-400">
                            <i class="fas fa-spinner mr-2"></i>Processing <span id="count-processing" class="ml-1 text-xs bg-yellow-600 px-2 py-0.5 rounded-full">0</span>
                        </button>
                        <button onclick="switchTab('queued_for_review')" id="tab-queued_for_review" class="px-6 py-3 font-medium text-gray-400">
                            <i class="fas fa-clipboard-check mr-2"></i>Review <span id="count-queued_for_review" class="ml-1 text-xs bg-purple-600 px-2 py-0.5 rounded-full">0</span>
                        </button>
                        <button onclick="switchTab('failed')" id="tab-failed" class="px-6 py-3 font-medium text-gray-400">
                            <i class="fas fa-exclamation-triangle mr-2"></i>Failed <span id="count-failed" class="ml-1 text-xs bg-red-600 px-2 py-0.5 rounded-full">0</span>
                        </button>
                        <button onclick="switchTab('reviews')" id="tab-reviews" class="px-6 py-3 font-medium text-gray-400">
                            <i class="fas fa-clipboard-list mr-2"></i>Pending Reviews <span id="count-reviews" class="ml-1 text-xs bg-green-600 px-2 py-0.5 rounded-full">0</span>
                        </button>
                        <button onclick="switchTab('logs')" id="tab-logs" class="px-6 py-3 font-medium text-gray-400">
                            <i class="fas fa-terminal mr-2"></i>Logs
                        </button>
                    </div>

                    <div id="panel-new" class="p-4">
                        <div class="flex flex-wrap gap-3 mb-4 pb-3 border-b border-gray-700 items-end">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Difficulty</label>
                                <select id="filterDifficulty" onchange="applyFilters()" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm">
                                    <option value="">All</option>
                                    <optgroup label="Easy">
                                        <option value="easy-1">Easy 1 (Trivial)</option>
                                        <option value="easy-2">Easy 2 (Simple)</option>
                                        <option value="easy-3">Easy 3 (Light)</option>
                                    </optgroup>
                                    <optgroup label="Medium">
                                        <option value="medium-1">Medium 1 (Straightforward)</option>
                                        <option value="medium-2">Medium 2 (Moderate)</option>
                                        <option value="medium-3">Medium 3 (Involved)</option>
                                    </optgroup>
                                    <optgroup label="Hard">
                                        <option value="hard-1">Hard 1 (Challenging)</option>
                                        <option value="hard-2">Hard 2 (Complex)</option>
                                        <option value="hard-3">Hard 3 (Expert)</option>
                                    </optgroup>
                                </select>
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Type</label>
                                <select id="filterClassification" onchange="applyFilters()" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm">
                                    <option value="">All</option>
                                    <option value="simple">Simple</option>
                                    <option value="complex">Complex</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Price Min</label>
                                <input type="number" id="filterPriceMin" value="" min="0" placeholder="0" onchange="applyFilters()" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm w-20">
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Price Max</label>
                                <input type="number" id="filterPriceMax" value="" min="0" placeholder="∞" onchange="applyFilters()" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm w-20">
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Sort By</label>
                                <select id="sortBy" onchange="applyFilters()" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm">
                                    <option value="fetched_desc">Newest</option>
                                    <option value="fetched_asc">Oldest</option>
                                    <option value="price_desc">Price (High)</option>
                                    <option value="price_asc">Price (Low)</option>
                                    <option value="difficulty_asc">Difficulty ↑</option>
                                    <option value="difficulty_desc">Difficulty ↓</option>
                                    <option value="score_desc">Score (High)</option>
                                </select>
                            </div>
                            <div class="flex items-end gap-2 ml-auto">
                                <span id="filteredCount" class="text-xs text-gray-500 mr-2"></span>
                                <button onclick="clearAllUntouched()" class="bg-red-600 hover:bg-red-700 px-3 py-1.5 rounded text-sm">
                                    <i class="fas fa-trash mr-1"></i> Clear All Untouched
                                </button>
                                <button onclick="deleteSelected()" id="deleteSelectedBtn" class="bg-red-600 hover:bg-red-700 px-3 py-1.5 rounded text-sm hidden">
                                    <i class="fas fa-trash mr-1"></i> Delete (<span id="selectedCount">0</span>)
                                </button>
                            </div>
                        </div>
                        <div id="tasksList" class="space-y-3">
                            <div class="text-gray-400">Click "Scan Tasks" to find available tasks</div>
                        </div>
                    </div>

                    <div id="panel-processing" class="p-4 hidden">
                        <div id="processingList" class="space-y-3">
                            <div class="text-gray-400">No tasks currently processing</div>
                        </div>
                    </div>

                    <div id="panel-queued_for_review" class="p-4 hidden">
                        <div id="queuedForReviewList" class="space-y-3">
                            <div class="text-gray-400">No tasks queued for review</div>
                        </div>
                    </div>

                    <div id="panel-failed" class="p-4 hidden">
                        <div class="flex flex-wrap gap-3 mb-4 pb-3 border-b border-gray-700 items-end">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Sort By</label>
                                <select id="sortByFailed" onchange="applyFilters()" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm">
                                    <option value="fetched_desc">Newest</option>
                                    <option value="fetched_asc">Oldest</option>
                                </select>
                            </div>
                            <div class="flex items-end gap-2 ml-auto">
                                <button onclick="retryAllFailed()" class="bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded text-sm">
                                    <i class="fas fa-redo mr-1"></i> Retry All Failed
                                </button>
                            </div>
                        </div>
                        <div id="failedList" class="space-y-3">
                            <div class="text-gray-400">No failed tasks</div>
                        </div>
                    </div>

                    <div id="panel-reviews" class="p-4 hidden">
                        <div id="reviewsList" class="space-y-3">
                            <div class="text-gray-400">No pending reviews</div>
                        </div>
                    </div>

                    <div id="panel-logs" class="p-4 hidden">
                        <div class="flex gap-3 mb-4">
                            <input type="number" id="logFilterBountyId" placeholder="Filter by ID (e.g. 1, 2, 3...)" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm w-64">
                            <button onclick="loadLogs()" class="bg-purple-600 hover:bg-purple-700 px-3 py-1.5 rounded text-sm">
                                <i class="fas fa-sync mr-1"></i> Refresh Logs
                            </button>
                            <button onclick="document.getElementById('logsContainer').innerHTML = ''" class="bg-gray-600 hover:bg-gray-700 px-3 py-1.5 rounded text-sm">
                                <i class="fas fa-trash mr-1"></i> Clear View
                            </button>
                        </div>
                        <div id="logStats" class="hidden mb-4 bg-gray-900 rounded p-4">
                            <h4 class="text-sm font-bold text-gray-300 mb-2"><i class="fas fa-chart-bar mr-1"></i> Model Stats</h4>
                            <div id="logStatsContent" class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs"></div>
                        </div>
                        <div id="logsContainer" class="bg-gray-900 rounded p-4 h-[600px] overflow-y-auto font-mono text-sm space-y-1">
                            <div class="text-gray-400">Click "Refresh Logs" to load system logs</div>
                        </div>
                    </div>
                </div>

                <div id="precheckModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50">
                    <div class="bg-gray-800 rounded-lg p-6 w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
                        <div class="flex justify-between items-center mb-4">
                            <h3 class="text-lg font-bold"><i class="fas fa-search mr-2"></i>Pre-Check Results</h3>
                            <button onclick="hidePrecheckModal()" class="text-gray-400 hover:text-white"><i class="fas fa-times"></i></button>
                        </div>

                        <div id="precheckWarnings" class="space-y-2 mb-4"></div>

                        <div class="mb-4 hidden">
                            <label class="block text-sm text-gray-400 mb-1"><i class="fas fa-book mr-1"></i>CONTRIBUTING.md Rules</label>
                            <div id="precheckContributing" class="text-xs text-gray-300 bg-gray-900 p-3 rounded font-mono whitespace-pre-wrap max-h-32 overflow-y-auto"></div>
                        </div>

                        <div class="mb-4">
                            <label class="block text-sm text-gray-400 mb-1"><i class="fas fa-comment mr-1"></i>Suggested Comment (copy & paste on GitHub)</label>
                            <textarea id="precheckComment" readonly class="w-full h-32 bg-gray-900 text-gray-300 text-sm font-mono p-3 rounded resize-none"></textarea>
                            <button id="copyCommentBtn" onclick="copyComment()" class="mt-2 bg-gray-700 hover:bg-gray-600 px-3 py-1.5 rounded text-sm">
                                <i class="fas fa-copy mr-1"></i> Copy to Clipboard
                            </button>
                        </div>

                        <div class="flex justify-end gap-3">
                            <button onclick="hidePrecheckModal()" class="bg-gray-600 hover:bg-gray-700 px-4 py-2 rounded text-sm">Cancel</button>
                            <button id="precheckProceedBtn" class="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded text-sm font-medium">
                                <i class="fas fa-play mr-1"></i> Proceed with Fix
                            </button>
                        </div>
                    </div>
                </div>

                <div id="processingModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50">
                    <div class="bg-gray-800 rounded-lg p-6 w-full max-w-lg mx-4">
                        <div class="flex justify-between items-center mb-4">
                            <h3 class="text-lg font-bold"><i class="fas fa-cog fa-spin mr-2"></i>Processing Task</h3>
                            <button onclick="hideProcessingModal()" class="text-gray-400 hover:text-white"><i class="fas fa-times"></i></button>
                        </div>
                        <div class="mb-4">
                            <div class="flex items-center justify-between mb-2">
                                <span id="processingStatusBadge" class="px-3 py-1 rounded text-sm font-medium bg-blue-600">Queued</span>
                                <span id="processingProgressText" class="text-sm text-gray-400">0%</span>
                            </div>
                            <div class="w-full bg-gray-700 rounded-full h-2">
                                <div id="processingProgressBar" class="bg-purple-600 h-2 rounded-full transition-all duration-300" style="width: 0%"></div>
                            </div>
                        </div>
                        <div id="processingLogContent" class="text-sm text-gray-300 font-mono bg-gray-900 p-3 rounded h-64 overflow-y-auto space-y-1"></div>
                    </div>
                </div>

                <div id="reviewDetailModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50">
                    <div class="bg-gray-800 rounded-lg p-6 w-full max-w-4xl mx-4 max-h-[90vh] overflow-y-auto">
                        <div class="flex justify-between items-center mb-4">
                            <h3 id="reviewDetailTitle" class="text-lg font-bold"><i class="fas fa-code mr-2"></i>Review Detail</h3>
                            <button onclick="closeReviewDetail()" class="text-gray-400 hover:text-white"><i class="fas fa-times"></i></button>
                        </div>
                        <pre id="reviewDetailContent" class="bg-gray-900 p-4 rounded text-xs font-mono whitespace-pre overflow-x-auto max-h-[70vh] overflow-y-auto"></pre>
                    </div>
                </div>

                <div id="settingsModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50">
                    <div class="bg-gray-800 rounded-lg p-6 w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
                        <div class="flex justify-between items-center mb-4">
                            <h3 class="text-lg font-bold"><i class="fas fa-cog mr-2"></i>Settings</h3>
                            <button onclick="closeSettings()" class="text-gray-400 hover:text-white"><i class="fas fa-times"></i></button>
                        </div>

                        <div class="space-y-6">
                            <div>
                                <h4 class="text-sm font-bold text-purple-400 mb-2"><i class="fas fa-box mr-1"></i> Sandbox</h4>
                                <div class="flex items-center gap-3 bg-gray-900 p-3 rounded">
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <input type="checkbox" id="cfgSandboxEnabled" class="accent-purple-500 w-4 h-4">
                                        <span class="text-sm">Enable sandbox execution (Podman/Docker)</span>
                                    </label>
                                </div>
                                <p class="text-xs text-gray-500 mt-1">When disabled, agents run LLM calls directly on the host without container isolation.</p>
                            </div>

                            <div>
                                <h4 class="text-sm font-bold text-purple-400 mb-2"><i class="fas fa-brain mr-1"></i> Ollama Models</h4>
                                <div class="grid grid-cols-2 gap-3">
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Classifier</label>
                                        <input type="text" id="cfgModelClassifier" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                    </div>
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Simple Agent</label>
                                        <input type="text" id="cfgModelSimple" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                    </div>
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Complex Agent</label>
                                        <input type="text" id="cfgModelComplex" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                    </div>
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Code Reviewer</label>
                                        <input type="text" id="cfgModelReviewer" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                    </div>
                                </div>
                                <div class="mt-2">
                                    <label class="block text-xs text-gray-400 mb-1">Base URL</label>
                                    <input type="text" id="cfgOllamaUrl" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                </div>
                            </div>

                            <div>
                                <h4 class="text-sm font-bold text-purple-400 mb-2"><i class="fas fa-code-branch mr-1"></i> Git</h4>
                                <div class="grid grid-cols-2 gap-3">
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Username</label>
                                        <input type="text" id="cfgGitUsername" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                    </div>
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Token</label>
                                        <input type="password" id="cfgGitToken" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                    </div>
                                </div>
                            </div>

                            <div>
                                <h4 class="text-sm font-bold text-purple-400 mb-2"><i class="fas fa-folder mr-1"></i> Workspace</h4>
                                <div>
                                    <label class="block text-xs text-gray-400 mb-1">Base Path</label>
                                    <input type="text" id="cfgWorkspacePath" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                </div>
                            </div>

                            <div>
                                <h4 class="text-sm font-bold text-purple-400 mb-2"><i class="fas fa-cloud mr-1"></i> OpenCode (Cloud)</h4>
                                <div class="grid grid-cols-2 gap-3">
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">API Key</label>
                                        <input type="password" id="cfgOpencodeKey" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                    </div>
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Base URL</label>
                                        <input type="text" id="cfgOpencodeUrl" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                    </div>
                                </div>
                            </div>

                            <div>
                                <h4 class="text-sm font-bold text-purple-400 mb-2"><i class="fas fa-flask mr-1"></i> Test Mode</h4>
                                <div class="flex items-center gap-3 bg-gray-900 p-3 rounded">
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <input type="checkbox" id="cfgTestMode" class="accent-purple-500 w-4 h-4">
                                        <span class="text-sm">Enable test mode (free tasks only)</span>
                                    </label>
                                </div>
                            </div>
                        </div>

                        <div class="flex justify-end gap-3 mt-6 pt-4 border-t border-gray-700">
                            <button onclick="closeSettings()" class="bg-gray-600 hover:bg-gray-700 px-4 py-2 rounded text-sm">Cancel</button>
                            <button onclick="saveSettings()" class="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded text-sm font-medium">
                                <i class="fas fa-save mr-1"></i> Save
                            </button>
                        </div>
                    </div>
                </div>
            </main>
        </div>

        <div id="scanModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50">
            <div class="bg-gray-800 rounded-lg p-6 w-full max-w-md mx-4">
                <h3 class="text-lg font-bold mb-4">Scan for Tasks</h3>

                <div class="space-y-4">
                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Mode</label>
                        <div class="flex space-x-4">
                            <label class="flex items-center space-x-2 cursor-pointer">
                                <input type="radio" name="scanMode" value="test" id="scanModeTest" checked onchange="updateScanMode()" class="accent-purple-500">
                                <span class="text-sm">Test (Free Tasks)</span>
                            </label>
                            <label class="flex items-center space-x-2 cursor-pointer">
                                <input type="radio" name="scanMode" value="prod" id="scanModeProd" onchange="updateScanMode()" class="accent-purple-500">
                                <span class="text-sm">Production (Bounty Issues)</span>
                            </label>
                        </div>
                    </div>

                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Price Range ($)</label>
                        <div class="flex items-center space-x-2">
                            <input type="number" id="minPrice" value="0" min="0" class="bg-gray-700 border border-gray-600 rounded px-3 py-2 w-24 text-sm">
                            <span class="text-gray-400">to</span>
                            <input type="number" id="maxPrice" value="0" min="0" class="bg-gray-700 border border-gray-600 rounded px-3 py-2 w-24 text-sm">
                        </div>
                        <p id="priceHint" class="text-xs text-gray-500 mt-1">Test mode: only free tasks ($0).</p>
                    </div>

                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Max Tasks</label>
                        <input type="number" id="maxTasks" value="10" min="1" max="50" class="bg-gray-700 border border-gray-600 rounded px-3 py-2 w-24 text-sm">
                    </div>

                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Custom Query (optional)</label>
                        <input type="text" id="customQuery" placeholder="e.g. label:bug language:python" class="bg-gray-700 border border-gray-600 rounded px-3 py-2 w-full text-sm">
                        <p id="scanHint" class="text-xs text-gray-500 mt-1">Test mode: searches for "good first issue" labels.</p>
                    </div>
                </div>

                <div class="flex justify-end space-x-3 mt-6">
                    <button onclick="closeScanModal()" class="bg-gray-600 hover:bg-gray-700 px-4 py-2 rounded text-sm">Cancel</button>
                    <button onclick="executeScan()" id="executeScanBtn" class="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded text-sm">
                        <i class="fas fa-search mr-1"></i> Scan
                    </button>
                </div>
            </div>
        </div>

        <script>
            let currentTab = 'new';

            function switchTab(tab) {
                currentTab = tab;
                const tabs = ['new', 'processing', 'queued_for_review', 'failed', 'reviews', 'logs'];
                tabs.forEach(t => {
                    const tabEl = document.getElementById('tab-' + t);
                    const panelEl = document.getElementById('panel-' + t);
                    if (tabEl) {
                        tabEl.className = t === tab ? 'tab-active px-6 py-3 font-medium' : 'px-6 py-3 font-medium text-gray-400';
                    }
                    if (panelEl) {
                        panelEl.className = t === tab ? 'p-4' : 'p-4 hidden';
                    }
                });
                if (tab === 'reviews') loadReviews();
                if (tab === 'logs') loadLogs();
                if (['new', 'processing', 'queued_for_review', 'failed'].includes(tab)) applyFilters();
            }

            function closeScanModal() {
                document.getElementById('scanModal').classList.add('hidden');
                document.getElementById('scanModal').classList.remove('flex');
            }

            function updateScanMode() {
                const isTest = document.getElementById('scanModeTest').checked;
                document.getElementById('minPrice').value = isTest ? 0 : 5;
                document.getElementById('maxPrice').value = isTest ? 0 : 150;
                document.getElementById('minPrice').disabled = isTest;
                document.getElementById('maxPrice').disabled = isTest;
                document.getElementById('minPrice').classList.toggle('opacity-50', isTest);
                document.getElementById('maxPrice').classList.toggle('opacity-50', isTest);
                document.getElementById('priceHint').textContent = isTest
                    ? 'Test mode: only free tasks ($0).'
                    : 'Production mode: editable price range. Adjust to filter bounties.';
                document.getElementById('priceHint').className = isTest ? 'text-xs text-yellow-500 mt-1' : 'text-xs text-gray-500 mt-1';
                document.getElementById('scanHint').textContent = isTest
                    ? 'Test mode: searches for "good first issue" labels.'
                    : 'Production mode: searches GitHub for issues mentioning bounties/rewards.';
            }

            async function executeScan() {
                const btn = document.getElementById('executeScanBtn');
                const testMode = document.getElementById('scanModeTest').checked;
                const minPrice = parseInt(document.getElementById('minPrice').value) || 0;
                const maxPrice = parseInt(document.getElementById('maxPrice').value) || 0;
                const limit = parseInt(document.getElementById('maxTasks').value) || 10;
                const customQuery = document.getElementById('customQuery').value.trim();

                btn.disabled = true;
                btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i> Scanning...';

                try {
                    const res = await fetch('/api/scan', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            test_mode: testMode,
                            min_price: minPrice,
                            max_price: maxPrice,
                            limit: limit,
                            query: customQuery || null
                        })
                    });
                    const data = await res.json();
                    alert(`Found ${data.tasks_found} tasks (price: $${minPrice}-${maxPrice})`);
                    loadTasks();
                    closeScanModal();
                } catch (e) {
                    alert('Scan failed: ' + e.message);
                } finally {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-search mr-1"></i> Scan';
                }
            }

            function viewTaskLogs(taskId) {
                document.getElementById('logFilterBountyId').value = taskId;
                switchTab('logs');
            }

            async function loadLogs() {
                const container = document.getElementById('logsContainer');
                const bountyId = document.getElementById('logFilterBountyId').value;
                container.innerHTML = '<div class="text-gray-400">Loading logs...</div>';

                const statsPanel = document.getElementById('logStats');
                const statsContent = document.getElementById('logStatsContent');
                statsPanel.classList.add('hidden');

                try {
                    const url = bountyId ? `/api/logs?bounty_id=${bountyId}` : '/api/logs';
                    const res = await fetch(url);
                    const logs = await res.json();

                    if (logs.length === 0) {
                        container.innerHTML = '<div class="text-gray-400">No logs found</div>';
                        return;
                    }

                    if (bountyId) {
                        try {
                            const statsRes = await fetch(`/api/tasks/${bountyId}/stats`);
                            const stats = await statsRes.json();
                            if (stats.total_tokens > 0 || stats.total_duration > 0) {
                                statsPanel.classList.remove('hidden');
                                let html = `
                                    <div class="bg-gray-800 rounded p-3">
                                        <div class="text-gray-500">Total Duration</div>
                                        <div class="text-lg font-bold text-purple-400">${stats.total_duration.toFixed(1)}s</div>
                                    </div>
                                    <div class="bg-gray-800 rounded p-3">
                                        <div class="text-gray-500">Total Tokens</div>
                                        <div class="text-lg font-bold text-green-400">${stats.total_tokens.toLocaleString()}</div>
                                        <div class="text-gray-500 text-[10px]">Prompt: ${stats.total_prompt_tokens.toLocaleString()} | Completion: ${stats.total_completion_tokens.toLocaleString()}</div>
                                    </div>
                                `;
                                for (const [modelName, m] of Object.entries(stats.models)) {
                                    html += `
                                        <div class="bg-gray-800 rounded p-3">
                                            <div class="text-gray-500 truncate" title="${modelName}">${modelName}</div>
                                            <div class="text-xs text-gray-300 mt-1">
                                                Tokens: ${m.total_tokens.toLocaleString()} (${m.tokens_per_sec} tok/s)
                                            </div>
                                            <div class="text-xs text-gray-300">
                                                Time: ${m.duration.toFixed(1)}s
                                            </div>
                                            <div class="text-xs text-gray-300">
                                                Prompt: ${m.prompt_tokens.toLocaleString()} | Comp: ${m.completion_tokens.toLocaleString()}
                                            </div>
                                        </div>
                                    `;
                                }
                                statsContent.innerHTML = html;
                            }
                        } catch (e) {
                            console.error('Failed to load stats:', e);
                        }
                    }

                    container.innerHTML = logs.map(l => {
                        const dt = new Date(l.created_at);
                        const dd = String(dt.getDate()).padStart(2, '0');
                        const mm = String(dt.getMonth() + 1).padStart(2, '0');
                        const yyyy = dt.getFullYear();
                        const hh = String(dt.getHours()).padStart(2, '0');
                        const min = String(dt.getMinutes()).padStart(2, '0');
                        const ss = String(dt.getSeconds()).padStart(2, '0');
                        const time = `${dd}/${mm}/${yyyy} ${hh}:${min}:${ss}`;
                        const color = l.status === 'error' || l.status === 'failed' ? 'text-red-400' :
                                      l.status === 'warning' ? 'text-yellow-400' : 'text-green-400';
                        const action = (l.action || l.step || '').replace(/</g, '&lt;');
                        const details = (l.details || '').replace(/</g, '&lt;');
                        const agent = (l.agent_type || 'system').replace(/</g, '&lt;');
                        const isInfo = action.startsWith('Model:') || action.includes('Decomposed') ||
                                       action.includes('Prompt:') || action.includes('Processing time') ||
                                       action.includes('Review time') || action.includes('Tokens');
                        return `<div class="border-b border-gray-800 pb-1 ${isInfo ? 'bg-gray-800/50 px-2 rounded' : ''}">
                            <span class="text-gray-500">[${time}]</span>
                            <span class="text-purple-400">[${agent}]</span>
                            <span class="${color}">${action}</span>
                            ${details ? `<span class="text-gray-300 ml-2 text-xs">${details}</span>` : ''}
                        </div>`;
                    }).join('');
                } catch (e) {
                    container.innerHTML = `<div class="text-red-400">Failed to load logs: ${e.message}</div>`;
                }
            }

            async function refreshStatus() {
                try {
                    const [statusRes, statsRes] = await Promise.all([
                        fetch('/api/status'),
                        fetch('/api/dashboard-stats')
                    ]);
                    const status = await statusRes.json();
                    const stats = await statsRes.json();

                    document.getElementById('systemStatus').className = 'status-dot ' + (status.running ? 'green' : 'red');
                    document.getElementById('systemStatusText').textContent = status.running ? 'Running' : 'Stopped';
                    if (stats.uptime) {
                        document.getElementById('uptimeText').textContent = stats.uptime;
                    }

                    if (stats.ollama) {
                        const o = stats.ollama;
                        document.getElementById('ollamaStatus').innerHTML = o.running ? '<span class="text-green-400">✓ Running</span>' : '<span class="text-red-400">✗ Not running</span>';
                        document.getElementById('ollamaModel').textContent = o.models ? o.models.join(', ') : '';
                    }

                    if (stats.sandbox) {
                        const s = stats.sandbox;
                        document.getElementById('sandboxStatus').innerHTML = s.available ? '<span class="text-green-400">✓ ' + s.runtime + '</span>' : '<span class="text-red-400">✗ Not available</span>';
                        document.getElementById('sandboxImage').textContent = (s.enabled ? '✓ Enabled' : '✗ Disabled') + (s.image_built ? ' | Image: bounty-sandbox:latest' : ' | Image: not built');
                    }

                    if (stats.today) {
                        const t = stats.today;
                        document.getElementById('todayProcessed').textContent = 'Processed: ' + t.processed;
                        document.getElementById('todaySuccess').textContent = 'Success: ' + t.success + ' (' + (t.processed > 0 ? Math.round(t.success / t.processed * 100) : 0) + '%)';
                        document.getElementById('todayAvgTime').textContent = 'Avg time: ' + (t.avg_duration ? t.avg_duration.toFixed(0) + 's' : '-');
                    }

                    if (stats.db) {
                        const d = stats.db;
                        document.getElementById('dbTotalBounties').textContent = 'Bounties: ' + d.total_bounties;
                        document.getElementById('dbTotalReviews').textContent = 'Reviews: ' + d.total_reviews;
                        document.getElementById('dbSize').textContent = d.db_size ? (d.db_size / 1024).toFixed(1) + ' KB' : '';
                    }

                    if (stats.tabs) {
                        const tb = stats.tabs;
                        document.getElementById('qvNew').innerHTML = 'New: <span class="font-bold text-blue-400">' + tb.new + '</span>';
                        document.getElementById('qvFailed').innerHTML = 'Failed: <span class="font-bold text-red-400">' + tb.failed + '</span>';
                        document.getElementById('qvReview').innerHTML = 'Review: <span class="font-bold text-purple-400">' + tb.queued_for_review + '</span>';
                    }
                } catch (e) { console.error('Status refresh failed:', e); }
            }

            async function loadTasks() {
                try {
                    const res = await fetch('/api/tasks');
                    window.allTasks = await res.json();
                    document.getElementById('taskCount').textContent = window.allTasks.length;
                    applyFilters();
                } catch (e) { console.error('Load tasks failed:', e); }
            }

            function refreshAll() { refreshStatus(); loadTasks(); loadReviews(); }

            async function openSettings() {
                const modal = document.getElementById('settingsModal');
                modal.classList.remove('hidden');
                modal.classList.add('flex');

                try {
                    const res = await fetch('/api/config');
                    const cfg = await res.json();

                    document.getElementById('cfgSandboxEnabled').checked = cfg.sandbox?.enabled !== false;
                    document.getElementById('cfgModelClassifier').value = cfg.ollama_models?.classifier || '';
                    document.getElementById('cfgModelSimple').value = cfg.ollama_models?.simple_agent || '';
                    document.getElementById('cfgModelComplex').value = cfg.ollama_models?.complex_agent || '';
                    document.getElementById('cfgModelReviewer').value = cfg.ollama_models?.code_reviewer || '';
                    document.getElementById('cfgOllamaUrl').value = cfg.ollama_base_url || '';
                    document.getElementById('cfgTestMode').checked = cfg.test_mode || false;
                    document.getElementById('cfgGitUsername').value = cfg.git?.username || '';
                    document.getElementById('cfgGitToken').value = cfg.git?.token || '';
                    document.getElementById('cfgWorkspacePath').value = cfg.workspace?.base_path || '';
                    document.getElementById('cfgOpencodeKey').value = cfg.opencode?.api_key || '';
                    document.getElementById('cfgOpencodeUrl').value = cfg.opencode?.base_url || '';
                } catch (e) {
                    console.error('Failed to load config:', e);
                }
            }

            function closeSettings() {
                const modal = document.getElementById('settingsModal');
                modal.classList.add('hidden');
                modal.classList.remove('flex');
            }

            async function saveSettings() {
                const data = {
                    sandbox: {
                        enabled: document.getElementById('cfgSandboxEnabled').checked,
                    },
                    ollama: {
                        models: {
                            classifier: document.getElementById('cfgModelClassifier').value,
                            simple_agent: document.getElementById('cfgModelSimple').value,
                            complex_agent: document.getElementById('cfgModelComplex').value,
                            code_reviewer: document.getElementById('cfgModelReviewer').value,
                        },
                    },
                    test_mode: {
                        enabled: document.getElementById('cfgTestMode').checked,
                    },
                };

                const gitUsername = document.getElementById('cfgGitUsername').value;
                const gitToken = document.getElementById('cfgGitToken').value;
                if (gitUsername || gitToken) {
                    data.git = {};
                    if (gitUsername) data.git.username = gitUsername;
                    if (gitToken) data.git.token = gitToken;
                }

                const workspacePath = document.getElementById('cfgWorkspacePath').value;
                if (workspacePath) data.workspace = { base_path: workspacePath };

                const opencodeKey = document.getElementById('cfgOpencodeKey').value;
                const opencodeUrl = document.getElementById('cfgOpencodeUrl').value;
                if (opencodeKey || opencodeUrl) {
                    data.opencode = {};
                    if (opencodeKey) data.opencode.api_key = opencodeKey;
                    if (opencodeUrl) data.opencode.base_url = opencodeUrl;
                }

                try {
                    const res = await fetch('/api/config', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(data)
                    });
                    const result = await res.json();
                    if (result.success) {
                        alert('Settings saved successfully. Some changes may require a restart.');
                        closeSettings();
                    } else {
                        alert('Failed to save: ' + (result.error || 'Unknown error'));
                    }
                } catch (e) {
                    alert('Failed to save settings: ' + e.message);
                }
            }

            function applyFilters() {
                if (!window.allTasks) return;
                const difficulty = document.getElementById('filterDifficulty').value;
                const classification = document.getElementById('filterClassification').value;
                const priceMin = parseFloat(document.getElementById('filterPriceMin').value) || null;
                const priceMax = parseFloat(document.getElementById('filterPriceMax').value) || null;
                const sortBy = document.getElementById('sortBy').value;
                const sortByFailed = document.getElementById('sortByFailed').value;

                function normalizeStatus(s) {
                    const st = (s || 'new') === 'pending' ? 'new' : s;
                    return st;
                }

                function isFailed(t) {
                    const st = normalizeStatus(t.processing_status);
                    return ['failed', 'validation_failed', 'review_failed', 'error'].includes(st);
                }

                function taskGroup(t) {
                    const st = normalizeStatus(t.processing_status);
                    if (st === 'new') return 'new';
                    if (st === 'processing') return 'processing';
                    if (st === 'queued_for_review') return 'queued_for_review';
                    if (isFailed(t)) return 'failed';
                    return 'other';
                }

                function filterTask(t) {
                    if (difficulty && t.difficulty !== difficulty) return false;
                    if (classification && t.classification !== classification) return false;
                    const price = t.price || 0;
                    if (priceMin !== null && !t.is_bounty && price < priceMin) return false;
                    if (priceMax !== null && !t.is_bounty && price > priceMax) return false;
                    if (priceMin !== null && t.is_bounty && price > 0 && price < priceMin) return false;
                    if (priceMax !== null && t.is_bounty && price > 0 && price > priceMax) return false;
                    return true;
                }

                const groups = { new: [], processing: [], queued_for_review: [], failed: [] };
                window.allTasks.forEach(t => {
                    const g = taskGroup(t);
                    if (groups[g] && filterTask(t)) {
                        groups[g].push(t);
                    }
                });

                const diffOrder = { 'easy-1': 1, 'easy-2': 2, 'easy-3': 3, 'medium-1': 4, 'medium-2': 5, 'medium-3': 6, 'hard-1': 7, 'hard-2': 8, 'hard-3': 9 };

                function sortTasks(arr, sortKey) {
                    arr.sort((a, b) => {
                        switch (sortKey) {
                            case 'fetched_desc': return new Date(b.fetched_at || 0) - new Date(a.fetched_at || 0);
                            case 'fetched_asc': return new Date(a.fetched_at || 0) - new Date(b.fetched_at || 0);
                            case 'price_desc': return (b.price || 0) - (a.price || 0);
                            case 'price_asc': return (a.price || 0) - (b.price || 0);
                            case 'difficulty_asc': return (diffOrder[a.difficulty] || 0) - (diffOrder[b.difficulty] || 0);
                            case 'difficulty_desc': return (diffOrder[b.difficulty] || 0) - (diffOrder[a.difficulty] || 0);
                            case 'score_desc': return (b.github_score || 0) - (a.github_score || 0);
                            default: return 0;
                        }
                    });
                }

                sortTasks(groups.new, sortBy);
                sortTasks(groups.processing, sortBy);
                sortTasks(groups.queued_for_review, sortBy);
                sortTasks(groups.failed, sortByFailed);

                // Update counts
                document.getElementById('count-new').textContent = groups.new.length;
                document.getElementById('count-processing').textContent = groups.processing.length;
                document.getElementById('count-queued_for_review').textContent = groups.queued_for_review.length;
                document.getElementById('count-failed').textContent = groups.failed.length;

                // Render current tab
                const isUntouched = (t) => ['new', 'pending'].includes(normalizeStatus(t.processing_status));
                const isProcessing = (t) => normalizeStatus(t.processing_status) === 'processing';

                function statusColor(s) {
                    const st = normalizeStatus(s);
                    if (st === 'new') return 'bg-blue-600';
                    if (st === 'processing') return 'bg-yellow-600';
                    if (st === 'queued_for_review') return 'bg-purple-600';
                    if (st === 'failed' || st === 'validation_failed' || st === 'review_failed' || st === 'error') return 'bg-red-600';
                    if (st === 'pr_created') return 'bg-green-600';
                    return 'bg-gray-600';
                }

                function difficultyBadge(d) {
                    if (!d) return 'text-gray-400';
                    if (d.startsWith('easy')) return 'text-green-400';
                    if (d.startsWith('medium')) return 'text-yellow-400';
                    if (d.startsWith('hard')) return 'text-red-400';
                    return 'text-gray-400';
                }

                function difficultyLabel(d) {
                    if (!d) return '-';
                    const [tier, level] = d.split('-');
                    const labels = { '1': 'Trivial', '2': 'Simple', '3': 'Light' };
                    if (tier === 'medium') labels['1'] = 'Straightforward', labels['2'] = 'Moderate', labels['3'] = 'Involved';
                    if (tier === 'hard') labels['1'] = 'Challenging', labels['2'] = 'Complex', labels['3'] = 'Expert';
                    return `${tier} ${level} (${labels[level] || ''})`;
                }

                function formatDate(d) {
                    if (!d) return '-';
                    const dt = new Date(d);
                    const dd = String(dt.getDate()).padStart(2, '0');
                    const mm = String(dt.getMonth() + 1).padStart(2, '0');
                    const yyyy = dt.getFullYear();
                    const hh = String(dt.getHours()).padStart(2, '0');
                    const min = String(dt.getMinutes()).padStart(2, '0');
                    return `${dd}/${mm}/${yyyy} ${hh}:${min}`;
                }

                function renderTaskCard(t, showCheckbox = true) {
                    return `
                    <div class="bg-gray-750 rounded-lg p-4 border border-gray-700 hover:border-purple-500 transition ${isProcessing(t) ? 'border-yellow-500' : ''}">
                        <div class="flex items-start gap-3">
                            ${showCheckbox ? `<input type="checkbox" class="task-checkbox accent-purple-500 w-4 h-4 mt-1" data-id="${t.id}" onchange="updateSelectedCount()" ${!isUntouched(t) ? 'disabled' : ''}>` : ''}
                            <div class="flex-1">
                                <h3 class="font-bold">${t.title}</h3>
                                <p class="text-gray-400 text-sm">${t.repository_name || 'Unknown'}</p>
                                <div class="flex flex-wrap gap-2 mt-2 text-xs">
                                    <span class="px-2 py-0.5 rounded bg-gray-600 font-mono">#${t.id}</span>
                                    <span class="px-2 py-0.5 rounded ${statusColor(t.processing_status)}">${normalizeStatus(t.processing_status) || 'new'}</span>
                                    <span class="${difficultyBadge(t.difficulty)}">${difficultyLabel(t.difficulty)}</span>
                                    ${t.is_bounty ? `<span class="px-2 py-0.5 rounded bg-amber-600 text-white">Bounty ${t.price ? '$' + t.price : 'TBD'}</span>` : `<span class="text-gray-400">$${t.price || 0}</span>`}
                                    ${t.classification ? `<span class="text-gray-400">${t.classification}</span>` : ''}
                                    <span class="text-gray-500">${formatDate(t.fetched_at)}</span>
                                    ${t.tags ? `<span class="text-gray-500 truncate max-w-[200px]">${t.tags.split(',').slice(0, 3).join(', ')}</span>` : ''}
                                </div>
                            </div>
                            <div class="flex gap-2 ml-2 shrink-0">
                                ${isUntouched(t) ? `<button onclick="processTask(${t.id})" class="bg-purple-600 hover:bg-purple-700 px-3 py-1.5 rounded text-sm font-medium"><i class="fas fa-play mr-1"></i> Process</button>` : ''}
                                ${isProcessing(t) ? `<button onclick="showProcessingModal(${t.id})" class="bg-yellow-600 hover:bg-yellow-700 px-3 py-1.5 rounded text-sm font-medium"><i class="fas fa-spinner fa-spin mr-1"></i> Processing</button>` : ''}
                                ${isFailed(t) ? `<button onclick="retryTask(${t.id})" class="bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded text-sm font-medium"><i class="fas fa-redo mr-1"></i> Retry</button>` : ''}
                                ${!isUntouched(t) ? `<button onclick="deleteTaskWorkspace(${t.id})" class="bg-red-600 hover:bg-red-700 px-3 py-1.5 rounded text-sm" title="Delete Local Files"><i class="fas fa-trash"></i></button>` : ''}
                                <button onclick="viewTaskLogs(${t.id})" class="bg-gray-600 hover:bg-gray-700 px-3 py-1.5 rounded text-sm" title="View Logs"><i class="fas fa-terminal"></i></button>
                                <a href="${t.issue_url}" target="_blank" class="bg-gray-600 hover:bg-gray-700 px-3 py-1.5 rounded text-sm"><i class="fas fa-external-link"></i></a>
                            </div>
                        </div>
                    </div>`;
                }

                function renderList(containerId, tasks, showCheckbox = true) {
                    const container = document.getElementById(containerId);
                    if (tasks.length === 0) {
                        container.innerHTML = '<div class="text-gray-400">No tasks in this group</div>';
                        return;
                    }
                    let html = '';
                    if (showCheckbox) {
                        html = `
                            <div class="flex items-center gap-3 mb-2 px-1">
                                <input type="checkbox" id="selectAll" onchange="toggleSelectAll()" class="accent-purple-500 w-4 h-4">
                                <label for="selectAll" class="text-xs text-gray-400 cursor-pointer">Select All</label>
                            </div>
                        `;
                    }
                    html += tasks.map(t => renderTaskCard(t, showCheckbox)).join('');
                    container.innerHTML = html;
                }

                renderList('tasksList', groups.new, true);
                renderList('processingList', groups.processing, false);
                renderList('queuedForReviewList', groups.queued_for_review, false);
                renderList('failedList', groups.failed, false);

                const filteredCount = groups.new.length;
                const totalCount = window.allTasks.filter(t => taskGroup(t) === 'new').length;
                document.getElementById('filteredCount').textContent = filteredCount !== totalCount ? `${filteredCount} of ${totalCount}` : '';
                updateSelectedCount();
            }

            async function loadReviews() {
                try {
                    const res = await fetch('/api/reviews?status=pending');
                    const reviews = await res.json();
                    const container = document.getElementById('reviewsList');
                    if (reviews.length === 0) { container.innerHTML = '<div class="text-gray-400">No pending reviews</div>'; return; }
                    
                    document.getElementById('count-reviews').textContent = reviews.length;
                    
                    window._reviewsData = {};
                    
                    container.innerHTML = '';
                    reviews.forEach(r => {
                        window._reviewsData[r.id] = r;

                        const price = r.price ? `$${r.price}` : 'Unpaid';
                        const confidence = r.confidence_score ? `${(r.confidence_score * 100).toFixed(0)}%` : 'N/A';
                        const validation = r.validation_passed ? '<span class="text-green-400">✓ Passed</span>' : '<span class="text-red-400">✗ Failed</span>';
                        const title = (r.title || 'Untitled').replace(/</g, '&lt;');
                        const repo = (r.repository_name || 'Unknown repo').replace(/</g, '&lt;');
                        const agent = (r.agent_type || 'unknown').replace(/</g, '&lt;');
                        const issueUrl = r.issue_url || '';
                        const taskId = r.bounty_id || r.id;
                        const workspacePath = (r.workspace_path && r.workspace_path !== 'None' && r.workspace_path !== 'null')
                            ? r.workspace_path
                            : null;

                        const div = document.createElement('div');
                        div.className = 'bg-gray-750 rounded-lg p-4 border border-gray-700';
                        div.innerHTML = `
                            <div class="flex justify-between items-start">
                                <div class="flex-1">
                                    <div class="flex items-center gap-2 mb-1">
                                        <span class="px-2 py-0.5 rounded bg-gray-600 font-mono text-xs">#${taskId}</span>
                                        <h3 class="font-bold">${title}</h3>
                                    </div>
                                    <p class="text-gray-400 text-sm">${repo}</p>
                                    <div class="flex flex-wrap gap-3 mt-2 text-xs">
                                        <span class="text-gray-400">Agent: ${agent}</span>
                                        <span class="text-gray-400">Confidence: ${confidence}</span>
                                        <span class="text-gray-400">Validation: ${validation}</span>
                                        <span class="text-amber-400 font-bold">${price}</span>
                                    </div>
                                </div>
                            </div>
                            <div class="mt-3 flex flex-wrap gap-2">
                                <button onclick="showReviewDiff(${r.id})" class="text-sm text-purple-400 hover:text-purple-300 bg-gray-700 px-3 py-1.5 rounded">
                                    <i class="fas fa-code mr-1"></i> View Diff
                                </button>
                                ${r.review_notes ? `<button onclick="showReviewComment(${r.id})" class="text-sm text-purple-400 hover:text-purple-300 bg-gray-700 px-3 py-1.5 rounded"><i class="fas fa-comment mr-1"></i> View Comment</button>` : ''}
                                ${workspacePath ? `<button onclick="openWorkspace('${workspacePath}')" class="text-sm text-blue-400 hover:text-blue-300 bg-gray-700 px-3 py-1.5 rounded"><i class="fas fa-folder-open mr-1"></i> Show in Finder</button>` : ''}
                                <button onclick="viewTaskLogs(${taskId})" class="text-sm text-gray-400 hover:text-gray-300 bg-gray-700 px-3 py-1.5 rounded"><i class="fas fa-terminal mr-1"></i> Logs</button>
                                <button onclick="approveReview(${r.id})" class="bg-green-600 hover:bg-green-700 px-3 py-1.5 rounded text-sm">Approve & PR</button>
                                <button onclick="rejectReview(${r.id})" class="bg-red-600 hover:bg-red-700 px-3 py-1.5 rounded text-sm">Reject</button>
                                <button onclick="skipReview(${r.id})" class="bg-gray-600 hover:bg-gray-700 px-3 py-1.5 rounded text-sm">Skip</button>
                                ${issueUrl ? `<a href="${issueUrl}" target="_blank" class="bg-gray-600 hover:bg-gray-700 px-3 py-1.5 rounded text-sm"><i class="fas fa-external-link mr-1"></i>Issue</a>` : ''}
                            </div>
                        `;
                        container.appendChild(div);
                    });
                } catch (e) { console.error('Load reviews failed:', e); }
            }

            function showReviewDiff(id) {
                const r = window._reviewsData[id];
                if (!r) return;
                const modal = document.getElementById('reviewDetailModal');
                document.getElementById('reviewDetailTitle').textContent = 'Code Diff - ' + (r.title || '');
                const pre = document.getElementById('reviewDetailContent');
                pre.textContent = r.diff_content || 'No diff available';
                modal.classList.remove('hidden');
                modal.classList.add('flex');
            }

            function showReviewComment(id) {
                const r = window._reviewsData[id];
                if (!r) return;
                const modal = document.getElementById('reviewDetailModal');
                document.getElementById('reviewDetailTitle').textContent = 'Suggested Comment - ' + (r.title || '');
                const pre = document.getElementById('reviewDetailContent');
                pre.textContent = r.review_notes || '';
                modal.classList.remove('hidden');
                modal.classList.add('flex');
            }

            function closeReviewDetail() {
                const modal = document.getElementById('reviewDetailModal');
                modal.classList.add('hidden');
                modal.classList.remove('flex');
            }

            function openWorkspace(path) {
                fetch('/api/open-workspace', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({path})
                }).then(r => r.json()).then(d => {
                    if (!d.success) alert('Failed to open: ' + d.error);
                });
            }

            async function processTask(id) {
                const precheck = await fetch('/api/tasks/' + id + '/precheck').then(r => r.json());
                if (precheck.error) {
                    alert('Pre-check failed: ' + precheck.error);
                    return;
                }
                showPrecheckModal(id, precheck);
            }

            function showPrecheckModal(taskId, precheck) {
                const modal = document.getElementById('precheckModal');
                modal.classList.remove('hidden');
                modal.classList.add('flex');

                const warningsContainer = document.getElementById('precheckWarnings');
                const commentBox = document.getElementById('precheckComment');
                const contributingBox = document.getElementById('precheckContributing');

                warningsContainer.innerHTML = '';
                if (precheck.warnings && precheck.warnings.length > 0) {
                    precheck.warnings.forEach(w => {
                        const div = document.createElement('div');
                        div.className = 'text-sm text-yellow-400 flex items-center gap-2';
                        div.innerHTML = `<i class="fas fa-exclamation-triangle"></i> ${w}`;
                        warningsContainer.appendChild(div);
                    });
                } else {
                    warningsContainer.innerHTML = '<div class="text-sm text-green-400"><i class="fas fa-check mr-1"></i> No issues detected</div>';
                }

                if (precheck.is_assigned) {
                    const assignBadge = document.createElement('span');
                    assignBadge.className = 'px-2 py-1 rounded bg-red-600 text-white text-xs';
                    assignBadge.textContent = 'Assigned to: ' + precheck.assignees.join(', ');
                    warningsContainer.appendChild(assignBadge);
                }

                if (precheck.recent_claims && precheck.recent_claims.length > 0) {
                    precheck.recent_claims.forEach(c => {
                        const div = document.createElement('div');
                        div.className = 'text-sm text-orange-400';
                        div.innerHTML = `<i class="fas fa-user mr-1"></i> @${c.user} claimed ${c.time}`;
                        warningsContainer.appendChild(div);
                    });
                }

                commentBox.value = precheck.suggested_comment || '';

                if (precheck.contributing_rules) {
                    contributingBox.parentElement.classList.remove('hidden');
                    contributingBox.textContent = precheck.contributing_rules;
                } else {
                    contributingBox.parentElement.classList.add('hidden');
                }

                document.getElementById('precheckProceedBtn').onclick = () => {
                    hidePrecheckModal();
                    
                    const task = window.allTasks.find(t => t.id === taskId);
                    if (task) task.processing_status = 'processing';
                    applyFilters();
                    
                    showProcessingModal(taskId);
                    fetch('/api/tasks/' + taskId + '/process', { method: 'POST' });
                };
            }

            function hidePrecheckModal() {
                const modal = document.getElementById('precheckModal');
                modal.classList.add('hidden');
                modal.classList.remove('flex');
            }

            function copyComment() {
                const commentBox = document.getElementById('precheckComment');
                commentBox.select();
                document.execCommand('copy');
                const btn = document.getElementById('copyCommentBtn');
                const original = btn.innerHTML;
                btn.innerHTML = '<i class="fas fa-check mr-1"></i> Copied';
                setTimeout(() => { btn.innerHTML = original; }, 2000);
            }

            function showProcessingModal(taskId) {
                const modal = document.getElementById('processingModal');
                modal.classList.remove('hidden');
                modal.classList.add('flex');
                
                if (window._pollInterval) {
                    clearInterval(window._pollInterval);
                }
                
                pollTaskStatus(taskId);
            }

            function hideProcessingModal() {
                const modal = document.getElementById('processingModal');
                modal.classList.add('hidden');
                modal.classList.remove('flex');
                if (window._pollInterval) {
                    clearInterval(window._pollInterval);
                    window._pollInterval = null;
                }
            }

            async function pollTaskStatus(taskId) {
                const logContainer = document.getElementById('processingLogContent');
                const progressBar = document.getElementById('processingProgressBar');
                const progressText = document.getElementById('processingProgressText');
                const statusBadge = document.getElementById('processingStatusBadge');

                logContainer.innerHTML = '';
                let lastLogCount = 0;

                window._pollInterval = setInterval(async () => {
                    try {
                        const [statusRes, logsRes] = await Promise.all([
                            fetch('/api/tasks/' + taskId + '/status'),
                            fetch('/api/tasks/' + taskId + '/logs')
                        ]);
                        const status = await statusRes.json();
                        const logs = await logsRes.json();

                        if (status.progress !== undefined) {
                            progressBar.style.width = status.progress + '%';
                            progressText.textContent = status.progress + '%';
                        }

                        if (status.step) {
                            progressText.textContent = status.step;
                        }

                        if (status.status === 'queued') {
                            statusBadge.className = 'px-3 py-1 rounded text-sm font-medium bg-blue-600';
                            statusBadge.textContent = 'Queued';
                        } else if (status.status === 'processing') {
                            statusBadge.className = 'px-3 py-1 rounded text-sm font-medium bg-yellow-600';
                            statusBadge.textContent = 'Processing';
                        } else if (status.status === 'completed') {
                            statusBadge.className = 'px-3 py-1 rounded text-sm font-medium bg-green-600';
                            statusBadge.textContent = 'Complete';
                        } else if (status.status === 'error') {
                            statusBadge.className = 'px-3 py-1 rounded text-sm font-medium bg-red-600';
                            statusBadge.textContent = 'Failed';
                        }

                        for (let i = lastLogCount; i < logs.length; i++) {
                            const entry = logs[i];
                            const div = document.createElement('div');
                            div.className = 'text-sm font-mono';
                            const dt = new Date(entry.timestamp);
                            const hh = String(dt.getHours()).padStart(2, '0');
                            const min = String(dt.getMinutes()).padStart(2, '0');
                            const ss = String(dt.getSeconds()).padStart(2, '0');
                            const time = `${hh}:${min}:${ss}`;
                            div.innerHTML = `<span class="text-gray-500">[${time}]</span> <span class="text-purple-400">${entry.step}</span>${entry.detail ? ' - ' + entry.detail : ''}`;
                            logContainer.appendChild(div);
                        }
                        lastLogCount = logs.length;
                        logContainer.scrollTop = logContainer.scrollHeight;

                        if (status.status === 'completed' || status.status === 'error') {
                            clearInterval(window._pollInterval);
                            window._pollInterval = null;
                            loadTasks();
                            if (status.status === 'error') {
                                const div = document.createElement('div');
                                div.className = 'text-sm font-mono text-red-400 mt-2';
                                div.textContent = 'Error: ' + (status.error || 'Unknown error');
                                logContainer.appendChild(div);
                            }
                        }
                    } catch (e) {
                        console.error('Poll failed:', e);
                    }
                }, 15000);
            }

            async function approveReview(id) {
                if (!confirm('Approve and create PR?')) return;
                const res = await fetch('/api/reviews/' + id + '/approve', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({}) });
                const data = await res.json();
                alert(data.pr_url ? 'PR Created: ' + data.pr_url : 'Approved!');
                loadReviews(); refreshStatus();
            }
            async function rejectReview(id) { const c = prompt('Reason:'); if (c === null) return; await fetch('/api/reviews/' + id + '/reject', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({comments: c}) }); loadReviews(); }
            async function skipReview(id) { await fetch('/api/reviews/' + id + '/skip', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({}) }); loadReviews(); }
            async function startSystem() { await fetch('/api/start', {method: 'POST'}); refreshStatus(); }
            async function stopSystem() { await fetch('/api/stop', {method: 'POST'}); refreshStatus(); }
            function toggleSelectAll() {
                const checked = document.getElementById('selectAll').checked;
                document.querySelectorAll('.task-checkbox:not(:disabled)').forEach(cb => cb.checked = checked);
                updateSelectedCount();
            }

            function updateSelectedCount() {
                const count = document.querySelectorAll('.task-checkbox:checked').length;
                const btn = document.getElementById('deleteSelectedBtn');
                document.getElementById('selectedCount').textContent = count;
                btn.classList.toggle('hidden', count === 0);
            }

            async function deleteSelected() {
                const ids = Array.from(document.querySelectorAll('.task-checkbox:checked')).map(cb => parseInt(cb.dataset.id));
                if (ids.length === 0) return;
                if (!confirm(`Delete ${ids.length} task(s)?`)) return;
                const res = await fetch('/api/tasks/delete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ids})
                });
                const data = await res.json();
                alert(`Deleted ${data.deleted} task(s)`);
                loadTasks();
            }

            async function retryTask(id) {
                if (!confirm('Retry this task? It will be reset and processed again immediately.')) return;
                try {
                    const res = await fetch('/api/tasks/' + id + '/retry', { method: 'POST' });
                    const data = await res.json();
                    if (data.success) {
                        loadTasks();
                        showProcessingModal(id);
                    } else {
                        alert('Retry failed: ' + (data.error || 'Unknown error'));
                    }
                } catch (e) {
                    alert('Retry failed: ' + e.message);
                }
            }

            async function deleteTaskWorkspace(id) {
                if (!confirm('Delete local files for this task? This cannot be undone.')) return;
                try {
                    const res = await fetch('/api/tasks/' + id + '/workspace', { method: 'DELETE' });
                    const data = await res.json();
                    if (data.success) {
                        alert('Local files deleted');
                        loadTasks();
                    } else {
                        alert('Failed: ' + (data.error || 'Unknown error'));
                    }
                } catch (e) {
                    alert('Failed: ' + e.message);
                }
            }

            async function clearAllUntouched() {
                if (!confirm('Delete all untouched (new/pending) tasks? This cannot be undone.')) return;
                const res = await fetch('/api/tasks/clear-untouched', { method: 'POST' });
                const data = await res.json();
                alert(`Cleared ${data.deleted} task(s)`);
                loadTasks();
            }

            async function retryAllFailed() {
                if (!window.allTasks) return;
                const isFailed = (t) => {
                    const st = (t.processing_status || 'new') === 'pending' ? 'new' : t.processing_status;
                    return ['failed', 'validation_failed', 'review_failed', 'error'].includes(st);
                };
                const failedIds = window.allTasks.filter(isFailed).map(t => t.id);
                if (failedIds.length === 0) {
                    alert('No failed tasks to retry');
                    return;
                }
                if (!confirm(`Retry ${failedIds.length} failed task(s)?`)) return;
                let successCount = 0;
                for (const id of failedIds) {
                    try {
                        const res = await fetch('/api/tasks/' + id + '/retry', { method: 'POST' });
                        const data = await res.json();
                        if (data.success) successCount++;
                    } catch (e) {
                        console.error('Retry failed for task', id, e);
                    }
                }
                alert(`Retried ${successCount} of ${failedIds.length} task(s)`);
                loadTasks();
            }

            setInterval(refreshAll, 15000);
            refreshAll();
        </script>
    </body>
    </html>
    '''


def init_orchestrator():
    global orchestrator
    from ..core.task_processor import task_processor
    orchestrator = BountyFactoryOrchestrator()
    task_processor.start()
    return orchestrator


@app.route('/api/status', methods=['GET'])
def get_status():
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500

    return jsonify(orchestrator.get_status())


@app.route('/api/dashboard-stats', methods=['GET'])
def dashboard_stats():
    global _startup_time
    import subprocess
    import os as _os

    stats = {}

    # Uptime
    if _startup_time:
        elapsed = time.time() - _startup_time
        hours = int(elapsed // 3600)
        mins = int((elapsed % 3600) // 60)
        stats['uptime'] = f'{hours}h {mins}m' if hours > 0 else f'{mins}m'
    else:
        stats['uptime'] = '-'

    # Ollama
    try:
        resp = subprocess.run(
            ['curl', '-s', 'http://localhost:11434/api/tags'],
            capture_output=True, text=True, timeout=5
        )
        if resp.returncode == 0:
            import json as _json
            data = _json.loads(resp.stdout)
            models = [m.get('name', '') for m in data.get('models', [])]
            stats['ollama'] = {'running': True, 'models': models}
        else:
            stats['ollama'] = {'running': False, 'models': []}
    except Exception:
        stats['ollama'] = {'running': False, 'models': []}

    # Sandbox
    runtime = None
    for rt in ['docker', 'podman']:
        try:
            r = subprocess.run([rt, 'info'], capture_output=True, timeout=5)
            if r.returncode == 0:
                runtime = rt
                break
        except Exception:
            continue

    image_built = False
    if runtime:
        try:
            r = subprocess.run([runtime, 'image', 'inspect', 'bounty-sandbox:latest'],
                               capture_output=True, timeout=5)
            image_built = r.returncode == 0
        except Exception:
            pass
    sandbox_enabled = config.get('sandbox', {}).get('enabled', True)
    stats['sandbox'] = {
        'available': runtime is not None,
        'runtime': runtime,
        'image_built': image_built,
        'enabled': sandbox_enabled,
    }

    # Today's stats from processing logs
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM processing_logs
            WHERE created_at >= date('now')
            AND action = 'complete'
        """)
        processed = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM processing_logs
            WHERE created_at >= date('now')
            AND status IN ('failed', 'error')
            AND action NOT LIKE '%Model:%' AND action NOT LIKE '%Token%'
        """)
        errors = cursor.fetchone()[0]

        cursor.execute("""
            SELECT AVG(CAST(SUBSTR(action, INSTR(action, ': ') + 2) AS REAL))
            FROM processing_logs
            WHERE created_at >= date('now')
            AND action LIKE 'Processing time: %'
        """)
        row = cursor.fetchone()
        avg_dur = row[0] if row and row[0] else None

        success = max(0, processed - errors)
        stats['today'] = {
            'processed': processed,
            'success': success,
            'avg_duration': avg_dur,
        }

    # Database stats
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM bounties")
        total_bounties = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM review_queue")
        total_reviews = cursor.fetchone()[0]
        stats['db'] = {'total_bounties': total_bounties, 'total_reviews': total_reviews}

    try:
        db_path = db.db_path
        if _os.path.exists(db_path):
            stats['db']['db_size'] = _os.path.getsize(db_path)
    except Exception:
        pass

    # Tab counts
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM bounties WHERE processing_status = 'new'")
        new_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM bounties WHERE processing_status IN ('failed', 'validation_failed', 'review_failed', 'error')")
        failed_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM bounties WHERE processing_status = 'queued_for_review'")
        review_count = cursor.fetchone()[0]
        stats['tabs'] = {
            'new': new_count,
            'failed': failed_count,
            'queued_for_review': review_count,
        }

    return jsonify(stats)


@app.route('/api/bounties', methods=['GET'])
def get_bounties():
    status_filter = request.args.get('status')

    with db.get_connection() as conn:
        cursor = conn.cursor()

        if status_filter:
            cursor.execute("""
                SELECT * FROM bounties
                WHERE processing_status = ?
                ORDER BY fetched_at DESC
                LIMIT 50
            """, (status_filter,))
        else:
            cursor.execute("""
                SELECT * FROM bounties
                ORDER BY fetched_at DESC
                LIMIT 50
            """)

        bounties = [dict(row) for row in cursor.fetchall()]

    return jsonify(bounties)


@app.route('/api/bounties/<int:bounty_id>', methods=['GET'])
def get_bounty(bounty_id):
    bounty = db.get_bounty_by_id(bounty_id)

    if not bounty:
        return jsonify({'error': 'Bounty not found'}), 404

    return jsonify(bounty)


@app.route('/api/open-workspace', methods=['POST'])
def open_workspace():
    import subprocess
    path = request.json.get('path')
    if not path:
        return jsonify({'error': 'No path provided'}), 400

    try:
        subprocess.run(['open', path], check=True)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/reviews', methods=['GET'])
def get_reviews():
    status_filter = request.args.get('status', 'pending')

    reviews = db.get_pending_reviews() if status_filter == 'pending' else []

    return jsonify(reviews)


@app.route('/api/reviews/<int:review_id>', methods=['GET'])
def get_review(review_id):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.*, b.title, b.description, b.repository_url, b.price, b.repository_name
            FROM review_queue r
            JOIN bounties b ON r.bounty_id = b.id
            WHERE r.id = ?
        """, (review_id,))

        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Review not found'}), 404

        review = dict(row)

    return jsonify(review)


@app.route('/api/reviews/<int:review_id>/approve', methods=['POST'])
def approve_review(review_id):
    comments = request.json.get('comments', '')

    reviews = db.get_pending_reviews()
    review = next((r for r in reviews if r['id'] == review_id), None)
    bounty_id = review['bounty_id'] if review else None

    db.update_review(review_id, 'approved', comments)

    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500

    pr_url = orchestrator.submit_pr(review_id)

    if bounty_id:
        from ..core.sandbox import cleanup_workspace
        cleanup_workspace(bounty_id)

    return jsonify({
        'success': True,
        'review_id': review_id,
        'pr_url': pr_url
    })


@app.route('/api/reviews/<int:review_id>/reject', methods=['POST'])
def reject_review(review_id):
    comments = request.json.get('comments', '')

    reviews = db.get_pending_reviews()
    review = next((r for r in reviews if r['id'] == review_id), None)
    bounty_id = review['bounty_id'] if review else None

    db.update_review(review_id, 'rejected', comments)

    if bounty_id:
        from ..core.sandbox import cleanup_workspace
        cleanup_workspace(bounty_id)

    return jsonify({
        'success': True,
        'review_id': review_id
    })


@app.route('/api/reviews/<int:review_id>/skip', methods=['POST'])
def skip_review(review_id):
    comments = request.json.get('comments', '')

    reviews = db.get_pending_reviews()
    review = next((r for r in reviews if r['id'] == review_id), None)
    bounty_id = review['bounty_id'] if review else None

    db.update_review(review_id, 'skipped', comments)

    if bounty_id:
        from ..core.sandbox import cleanup_workspace
        cleanup_workspace(bounty_id)

    return jsonify({
        'success': True,
        'review_id': review_id
    })


@app.route('/api/start', methods=['POST'])
def start_orchestrator():
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500

    orchestrator.start()

    return jsonify({'success': True, 'message': 'Orchestrator started'})


@app.route('/api/stop', methods=['POST'])
def stop_orchestrator():
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500

    orchestrator.stop()

    return jsonify({'success': True, 'message': 'Orchestrator stopped'})


@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    bounties = db.get_all_bounties()
    for b in bounties:
        for key in ('fetched_at', 'created_at', 'updated_at'):
            if b.get(key) and 'Z' not in str(b[key]) and '+' not in str(b[key]):
                b[key] = str(b[key]) + '+00:00'
    return jsonify(bounties)


@app.route('/api/tasks/running', methods=['GET'])
def get_running_tasks():
    count = db.get_running_tasks_count()
    return jsonify({'count': count})


@app.route('/api/config', methods=['GET'])
def get_config():
    import yaml
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'config', 'config.yaml')
    try:
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
        git_cfg = cfg.get('git', {})
        token = git_cfg.get('token', '')
        masked_token = token[:4] + '...' + token[-4:] if len(token) > 8 else ('****' if token and token != 'YOUR_GITHUB_TOKEN' else '')
        opencode_cfg = cfg.get('opencode', {})
        api_key = opencode_cfg.get('api_key', '')
        masked_key = api_key[:4] + '...' + api_key[-4:] if len(api_key) > 8 else ('****' if api_key and api_key != 'YOUR_OPENCODE_API_KEY' else '')
        return jsonify({
            'opencode': {
                'api_key_set': api_key != 'YOUR_OPENCODE_API_KEY',
                'api_key': masked_key,
                'base_url': opencode_cfg.get('base_url', ''),
            },
            'git': {
                'configured': git_cfg.get('username', '') != 'YOUR_GITHUB_USERNAME',
                'username': git_cfg.get('username', ''),
                'token': masked_token,
            },
            'test_mode': cfg.get('test_mode', {}).get('enabled', False),
            'ollama_models': cfg.get('ollama', {}).get('models', {}),
            'ollama_base_url': cfg.get('ollama', {}).get('base_url', ''),
            'sandbox': cfg.get('sandbox', {}),
            'workspace': cfg.get('workspace', {}),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config', methods=['POST'])
def update_config():
    import yaml
    from ..core.config import config as app_config
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'config', 'config.yaml')
    try:
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)

        data = request.json

        if 'test_mode' in data:
            cfg['test_mode']['enabled'] = bool(data['test_mode'].get('enabled', cfg['test_mode'].get('enabled')))

        if 'ollama' in data:
            o = data['ollama']
            if 'base_url' in o:
                cfg['ollama']['base_url'] = o['base_url']
            if 'models' in o:
                for k, v in o['models'].items():
                    if v:
                        cfg['ollama']['models'][k] = v

        if 'sandbox' in data:
            s = data['sandbox']
            if 'enabled' in s:
                cfg['sandbox']['enabled'] = bool(s['enabled'])

        if 'git' in data:
            g = data['git']
            if 'username' in g:
                cfg['git']['username'] = g['username']
            if 'token' in g:
                cfg['git']['token'] = g['token']

        if 'workspace' in data:
            w = data['workspace']
            if 'base_path' in w:
                cfg['workspace']['base_path'] = w['base_path']

        if 'opencode' in data:
            oc = data['opencode']
            if 'api_key' in oc:
                cfg['opencode']['api_key'] = oc['api_key']
            if 'base_url' in oc:
                cfg['opencode']['base_url'] = oc['base_url']

        app_config._config = cfg

        with open(config_path, 'w') as f:
            yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/logs', methods=['GET'])
def get_logs():
    bounty_id = request.args.get('bounty_id', type=int)
    limit = request.args.get('limit', 50, type=int)
    logs = db.get_processing_logs(bounty_id=bounty_id, limit=limit)
    for log in logs:
        if log.get('created_at') and 'Z' not in str(log['created_at']) and '+' not in str(log['created_at']):
            log['created_at'] = str(log['created_at']) + '+00:00'
    return jsonify(logs)


@app.route('/api/tasks/<int:task_id>/stats', methods=['GET'])
def task_stats(task_id):
    logs = db.get_processing_logs(bounty_id=task_id, limit=200)
    stats = {
        'models': {},
        'total_duration': 0,
        'total_prompt_tokens': 0,
        'total_completion_tokens': 0,
        'total_tokens': 0,
        'steps': [],
    }
    import re
    for log in logs:
        agent = log.get('agent_type') or ''
        action = log.get('action') or ''
        details = log.get('details') or ''
        status = log.get('status') or ''
        text = action if action else details

        # Model info can be in any agent_type where action starts with 'Model: '
        if action.startswith('Model: '):
            model_name = action[len('Model: '):].strip()
            if model_name not in stats['models']:
                stats['models'][model_name] = {
                    'prompt_tokens': 0,
                    'completion_tokens': 0,
                    'total_tokens': 0,
                    'duration': 0,
                    'count': 0,
                }
            stats['models'][model_name]['count'] += 1

        # Token stats: look for 'Prompt: X | Completion: Y | Total: Z' in action or details
        if 'Prompt:' in text and 'Completion:' in text:
            prompt_m = re.search(r'Prompt:\s*([\d?]+)', text)
            comp_m = re.search(r'Completion:\s*([\d?]+)', text)
            total_m = re.search(r'Total:\s*([\d?]+)', text)
            # Use the last model that was logged
            current_model = list(stats['models'].keys())[-1] if stats['models'] else None
            if current_model:
                if prompt_m and prompt_m.group(1) != '?':
                    pt = int(prompt_m.group(1))
                    stats['total_prompt_tokens'] += pt
                    stats['models'][current_model]['prompt_tokens'] += pt
                if comp_m and comp_m.group(1) != '?':
                    ct = int(comp_m.group(1))
                    stats['total_completion_tokens'] += ct
                    stats['models'][current_model]['completion_tokens'] += ct
                if total_m and total_m.group(1) != '?':
                    tt = int(total_m.group(1))
                    stats['total_tokens'] += tt
                    stats['models'][current_model]['total_tokens'] += tt

        # Duration: look for 'Processing time: Xs' or 'Review time: Xs' or 'Xs' pattern
        if 'time:' in text.lower() or (agent == 'duration'):
            dur_m = re.search(r'([\d.]+)s', text)
            if dur_m:
                dur = float(dur_m.group(1))
                stats['total_duration'] += dur
                current_model = list(stats['models'].keys())[-1] if stats['models'] else None
                if current_model:
                    stats['models'][current_model]['duration'] += dur

        if status in ('processing', 'completed', 'failed', 'error', 'warning'):
            stats['steps'].append({
                'agent': agent,
                'action': action,
                'status': status,
                'details': details[:200] if details else '',
                'timestamp': log.get('created_at'),
            })

    for model_name, m in stats['models'].items():
        if m['duration'] > 0 and m['total_tokens'] > 0:
            m['tokens_per_sec'] = round(m['total_tokens'] / m['duration'], 1)
        else:
            m['tokens_per_sec'] = 0

    return jsonify(stats)


@app.route('/api/tasks/<int:task_id>/retry', methods=['POST'])
def retry_task(task_id):
    bounty = db.get_bounty_by_id(task_id)
    if not bounty:
        return jsonify({'error': 'Task not found'}), 404

    db.update_bounty_status(task_id, 'new')
    db.log_processing(task_id, 'system', 'retried', 'new', 'Task reset to new status for retry')
    
    task_id_str = str(task_id)
    if task_id_str in task_processor._status:
        del task_processor._status[task_id_str]
    if task_id_str in task_processor._logs:
        del task_processor._logs[task_id_str]
    
    result = orchestrator.process_single_bounty(task_id)
    return jsonify({'success': True, 'message': 'Task restarted', 'auto_started': True})


@app.route('/api/tasks/<int:task_id>/precheck', methods=['GET'])
def precheck_task(task_id):
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500
    result = orchestrator.pre_check_bounty(task_id)
    return jsonify(result)


@app.route('/api/tasks/<int:task_id>/process', methods=['POST'])
def process_task(task_id):
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500
    result = orchestrator.process_single_bounty(task_id)
    return jsonify(result)


@app.route('/api/tasks/<int:task_id>/workspace', methods=['DELETE'])
def delete_task_workspace(task_id):
    from ..core.sandbox import cleanup_workspace
    from pathlib import Path
    from ..core.config import config

    workspace_base = config.get('workspace.base_path')
    workspace_dir = Path(workspace_base) / f'bounty_{task_id}'

    if not workspace_dir.exists():
        return jsonify({'success': False, 'error': 'No local files found'}), 404

    success = cleanup_workspace(task_id)
    return jsonify({'success': success, 'deleted': workspace_dir.name if success else None})


@app.route('/api/tasks/<int:task_id>/status', methods=['GET'])
def task_status(task_id):
    status = task_processor.get_status(str(task_id))
    if not status:
        return jsonify({'status': 'unknown', 'error': 'No status found for task'})
    return jsonify(status)


@app.route('/api/tasks/<int:task_id>/logs', methods=['GET'])
def task_logs(task_id):
    logs = task_processor.get_logs(str(task_id))
    return jsonify(logs)


@app.route('/api/tasks/clear-untouched', methods=['POST'])
def clear_untouched_tasks():
    deleted = db.cleanup_stale_tasks(days=0)
    return jsonify({'success': True, 'deleted': deleted})


def run_server(port: int = 5000, debug: bool = False, return_app: bool = False):
    global orchestrator, _startup_time
    from ..core.task_processor import task_processor
    _startup_time = time.time()
    orchestrator = BountyFactoryOrchestrator()
    task_processor.start()

    if return_app:
        import threading
        flask_thread = threading.Thread(
            target=lambda: app.run(host='0.0.0.0', port=port, debug=False),
            daemon=True,
        )
        flask_thread.start()
        return orchestrator

    app.run(host='0.0.0.0', port=port, debug=debug)


if __name__ == '__main__':
    run_server()