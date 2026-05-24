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


@app.route('/api/health')
def health_check():
    from datetime import datetime
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.utcnow().isoformat(),
        'orchestrator_running': orchestrator is not None and orchestrator.running,
    })


@app.route('/')
def serve_web_ui():
    if WEB_UI_PATH.exists():
        return send_file(WEB_UI_PATH)
    return '''
    <html>
    <head><title>Bounty Factory</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; }
        .status-dot.green { background-color: #22c55e; }
        .status-dot.red { background-color: #ef4444; }
        .status-dot.yellow { background-color: #eab308; }
        .tab-active { border-bottom: 2px solid #a855f7; color: #a855f7; }
        .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
        .scrollbar-hide::-webkit-scrollbar { display: none; }
        .stat-card { background: #111827; border-radius: 0.5rem; padding: 0.5rem 0.75rem; display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; transition: box-shadow 0.3s; }
        .stat-card .label { color: #9ca3af; font-size: 0.75rem; white-space: nowrap; }
        .stat-card .value { font-size: 0.875rem; font-weight: 700; font-family: 'Courier New', monospace; white-space: nowrap; }
        .pulse .stat-card { animation: pulse-border 0.6s ease-out; }
        @keyframes pulse-border { 0% { box-shadow: 0 0 0 0 rgba(168,85,247,0.4); } 50% { box-shadow: 0 0 0 4px rgba(168,85,247,0.2); } 100% { box-shadow: 0 0 0 0 rgba(168,85,247,0); } }
        .phase-dots { display: flex; align-items: center; justify-content: center; gap: 4px; margin-top: 2px; }
        .phase-dot { width: 8px; height: 8px; border-radius: 50%; }
        .phase-dot.waiting { background: #374151; }
        .phase-dot.active { background: #a855f7; animation: pulse-dot 1s ease-in-out infinite; }
        .phase-dot.done { background: #22c55e; }
        .phase-dot.failed { background: #ef4444; }
        @keyframes pulse-dot { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
        .tstat-good { color: #22c55e; }
        .tstat-warn { color: #eab308; }
        .tstat-bad { color: #ef4444; }
        .tstat-muted { color: #9ca3af; }
        .mini-bar { background: #374151; border-radius: 9999px; height: 4px; margin-top: 3px; overflow: hidden; }
        .mini-bar-fill { height: 100%; border-radius: 9999px; background: #a855f7; transition: width 0.5s ease; }
    </style>
    </head>
    <body class="bg-gray-900 text-white">
        <div class="min-h-screen">
            <nav class="bg-gray-800 border-b border-gray-700">
                <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between h-auto sm:h-16 p-3 sm:p-0 gap-3">
                        <div class="flex items-center w-full sm:w-auto justify-between">
                            <div class="flex items-center">
                                <i class="fas fa-robot text-2xl text-purple-500 mr-3"></i>
                                <span class="text-xl font-bold">Bounty Factory</span>
                            </div>
                            <button onclick="document.getElementById('navMenu').classList.toggle('hidden')" class="sm:hidden text-gray-400 hover:text-white p-2">
                                <i class="fas fa-bars text-lg"></i>
                            </button>
                        </div>
                        <div id="navMenu" class="hidden sm:flex items-center space-x-2 w-full sm:w-auto flex-col sm:flex-row gap-2 sm:gap-3">
                            <button onclick="openScanModal(); document.getElementById('navMenu').classList.add('hidden')" id="scanBtn" class="bg-purple-600 hover:bg-purple-700 px-3 py-2 rounded text-sm sm:text-base w-full sm:w-auto">
                                <i class="fas fa-search mr-1"></i> Scan Tasks
                            </button>
                            <button onclick="toggleSystem(); document.getElementById('navMenu').classList.add('hidden')" id="systemToggle" class="bg-gray-700 hover:bg-gray-600 px-3 py-2 rounded flex items-center justify-center gap-2 text-sm sm:text-base w-full sm:w-auto">
                                <span id="systemToggleDot" class="status-dot red"></span>
                                <span id="systemToggleText">Start</span>
                            </button>
                            <button onclick="toggleSandbox(); document.getElementById('navMenu').classList.add('hidden')" id="sandboxToggle" class="bg-gray-700 hover:bg-gray-600 px-3 py-2 rounded flex items-center justify-center gap-2 text-sm sm:text-base w-full sm:w-auto">
                                <span id="sandboxToggleDot" class="status-dot yellow"></span>
                                <span id="sandboxToggleText">Sandbox</span>
                            </button>
                            <div class="flex items-center gap-2 w-full sm:w-auto">
                                <button onclick="openSettings(); document.getElementById('navMenu').classList.add('hidden')" class="bg-gray-600 hover:bg-gray-700 px-3 py-2 rounded flex-1 sm:flex-none" title="Settings">
                                    <i class="fas fa-cog"></i>
                                </button>
                                <button onclick="refreshAll(); document.getElementById('navMenu').classList.add('hidden')" class="bg-gray-600 hover:bg-gray-700 px-3 py-2 rounded flex-1 sm:flex-none">
                                    <i class="fas fa-sync"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </nav>

            <main class="max-w-7xl mx-auto px-3 sm:px-6 lg:px-8 py-4 sm:py-6">
                <div class="bg-gray-800 rounded-lg p-3 sm:p-4 mb-4 sm:mb-6">
                    <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 sm:gap-4">
                        <div class="grid grid-cols-4 sm:flex sm:items-center sm:gap-6">
                            <div class="py-1 sm:py-0">
                                <div class="text-gray-400 text-[10px] sm:text-xs uppercase tracking-wide">CPU</div>
                                <div id="sysCpu" class="text-sm sm:text-lg font-mono font-bold">-</div>
                            </div>
                            <div class="hidden sm:block h-8 w-px bg-gray-700"></div>
                            <div class="py-1 sm:py-0">
                                <div class="text-gray-400 text-[10px] sm:text-xs uppercase tracking-wide">RAM</div>
                                <div id="sysRam" class="text-sm sm:text-lg font-mono font-bold">-</div>
                            </div>
                            <div class="hidden sm:block h-8 w-px bg-gray-700"></div>
                            <div class="py-1 sm:py-0">
                                <div class="text-gray-400 text-[10px] sm:text-xs uppercase tracking-wide">Disk</div>
                                <div id="sysDisk" class="text-sm sm:text-lg font-mono font-bold">-</div>
                            </div>
                            <div class="hidden sm:block h-8 w-px bg-gray-700"></div>
                            <div class="py-1 sm:py-0">
                                <div class="text-gray-400 text-[10px] sm:text-xs uppercase tracking-wide">Uptime</div>
                                <div id="uptimeText" class="text-sm sm:text-lg font-mono font-bold">-</div>
                            </div>
                        </div>
                        <div class="grid grid-cols-3 sm:flex sm:items-center sm:gap-6 text-xs border-t sm:border-t-0 border-gray-700 pt-2 sm:pt-0">
                            <div class="py-1 sm:py-0">
                                <div class="text-gray-400 text-[10px] sm:text-xs uppercase tracking-wide">Ollama</div>
                                <div id="sysOllamaModels" class="text-gray-300 mt-0.5 text-xs">-</div>
                            </div>
                            <div class="hidden sm:block h-8 w-px bg-gray-700"></div>
                            <div class="py-1 sm:py-0">
                                <div class="text-gray-400 text-[10px] sm:text-xs uppercase tracking-wide">Containers</div>
                                <div id="sysContainers" class="text-gray-300 mt-0.5 text-xs">-</div>
                            </div>
                            <div class="hidden sm:block h-8 w-px bg-gray-700"></div>
                            <div class="py-1 sm:py-0">
                                <div class="text-gray-400 text-[10px] sm:text-xs uppercase tracking-wide">Agents</div>
                                <div id="sysAgents" class="text-purple-400 mt-0.5 text-xs">-</div>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="bg-gray-800 rounded-lg mb-4 sm:mb-6">
                    <div class="flex overflow-x-auto border-b border-gray-700" style="-webkit-overflow-scrolling: touch;">
                        <button onclick="switchTab('new')" id="tab-new" class="tab-active px-2 sm:px-4 py-3 font-medium whitespace-nowrap text-xs sm:text-sm">
                            <i class="fas fa-inbox mr-1 sm:mr-2"></i>New <span id="count-new" class="ml-1 text-xs bg-blue-600 px-2 py-0.5 rounded-full">0</span>
                        </button>
                        <button onclick="switchTab('awaiting_assignment')" id="tab-awaiting_assignment" class="px-2 sm:px-4 py-3 font-medium text-gray-400 whitespace-nowrap text-xs sm:text-sm">
                            <i class="fas fa-clock mr-1 sm:mr-2"></i>Awaiting Assign <span id="count-awaiting_assignment" class="ml-1 text-xs bg-cyan-600 px-2 py-0.5 rounded-full">0</span>
                        </button>
                        <button onclick="switchTab('processing')" id="tab-processing" class="px-2 sm:px-4 py-3 font-medium text-gray-400 whitespace-nowrap text-xs sm:text-sm">
                            <i class="fas fa-spinner mr-1 sm:mr-2"></i>Processing <span id="count-processing" class="ml-1 text-xs bg-yellow-600 px-2 py-0.5 rounded-full">0</span>
                        </button>
                        <button onclick="switchTab('failed')" id="tab-failed" class="px-2 sm:px-4 py-3 font-medium text-gray-400 whitespace-nowrap text-xs sm:text-sm">
                            <i class="fas fa-exclamation-triangle mr-1 sm:mr-2"></i>Failed <span id="count-failed" class="ml-1 text-xs bg-red-600 px-2 py-0.5 rounded-full">0</span>
                        </button>
                        <button onclick="switchTab('reviews')" id="tab-reviews" class="px-2 sm:px-4 py-3 font-medium text-gray-400 whitespace-nowrap text-xs sm:text-sm">
                            <i class="fas fa-clipboard-check mr-1 sm:mr-2"></i>Pending Reviews <span id="count-reviews" class="ml-1 text-xs bg-green-600 px-2 py-0.5 rounded-full">0</span>
                        </button>
                        <button onclick="switchTab('rejected')" id="tab-rejected" class="px-2 sm:px-4 py-3 font-medium text-gray-400 whitespace-nowrap text-xs sm:text-sm">
                            <i class="fas fa-ban mr-1 sm:mr-2"></i>Rejected <span id="count-rejected" class="ml-1 text-xs bg-red-600 px-2 py-0.5 rounded-full">0</span>
                        </button>
                        <button onclick="switchTab('logs')" id="tab-logs" class="px-2 sm:px-4 py-3 font-medium text-gray-400 whitespace-nowrap text-xs sm:text-sm">
                            <i class="fas fa-terminal mr-1 sm:mr-2"></i>Logs
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

                    <div id="panel-awaiting_assignment" class="p-4 hidden">
                        <div class="flex items-end gap-2 mb-4 pb-3 border-b border-gray-700">
                            <div class="text-sm text-cyan-400"><i class="fas fa-clock mr-1"></i> /attempt posted — waiting for maintainer assignment before execution</div>
                        </div>
                        <div id="awaitingAssignmentList" class="space-y-3">
                            <div class="text-gray-400">No tasks awaiting assignment</div>
                        </div>
                    </div>

                    <div id="panel-processing" class="p-4 hidden">
                        <div class="flex items-end gap-2 mb-4 pb-3 border-b border-gray-700">
                            <div class="ml-auto">
                                <button onclick="resetAllProcessing()" class="bg-yellow-600 hover:bg-yellow-700 px-3 py-1.5 rounded text-sm">
                                    <i class="fas fa-undo mr-1"></i> Reset All Stuck
                                </button>
                            </div>
                        </div>
                        <div id="processingList" class="space-y-3">
                            <div class="text-gray-400">No tasks currently processing</div>
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
                                <button onclick="deleteAllFailed()" class="bg-red-600 hover:bg-red-700 px-3 py-1.5 rounded text-sm">
                                    <i class="fas fa-trash mr-1"></i> Delete All Failed
                                </button>
                            </div>
                        </div>
                        <div id="failedList" class="space-y-3">
                            <div class="text-gray-400">No failed tasks</div>
                        </div>
                    </div>

                    <div id="panel-reviews" class="p-4 hidden">
                        <div class="flex items-center gap-3 mb-4 pb-3 border-b border-gray-700">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Sort By</label>
                                <select id="sortByReviews" onchange="loadReviews()" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm">
                                    <option value="created_desc">Created (Newest)</option>
                                    <option value="created_asc">Created (Oldest)</option>
                                    <option value="finished_desc">Finished (Newest)</option>
                                    <option value="finished_asc">Finished (Oldest)</option>
                                </select>
                            </div>
                        </div>
                        <div id="reviewsList" class="space-y-3">
                            <div class="text-gray-400">No pending reviews</div>
                        </div>
                    </div>

                    <div id="panel-rejected" class="p-4 hidden">
                        <div class="flex flex-wrap gap-3 mb-4 pb-3 border-b border-gray-700 items-end">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Sort By</label>
                                <select id="sortByRejected" onchange="loadRejectedReviews()" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm">
                                    <option value="finished_desc">Rejected (Newest)</option>
                                    <option value="finished_asc">Rejected (Oldest)</option>
                                    <option value="created_desc">Created (Newest)</option>
                                    <option value="created_asc">Created (Oldest)</option>
                                </select>
                            </div>
                            <div class="flex items-end gap-2 ml-auto">
                                <button onclick="retryAllRejected()" class="bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded text-sm">
                                    <i class="fas fa-redo mr-1"></i> Retry All
                                </button>
                                <button onclick="deleteAllRejected()" class="bg-red-600 hover:bg-red-700 px-3 py-1.5 rounded text-sm">
                                    <i class="fas fa-trash mr-1"></i> Delete All
                                </button>
                            </div>
                        </div>
                        <div id="rejectedList" class="space-y-3">
                            <div class="text-gray-400">No rejected reviews</div>
                        </div>
                    </div>

                    <div id="panel-logs" class="p-4 hidden">
                        <div class="flex flex-wrap gap-3 mb-4">
                            <input type="number" id="logFilterBountyId" placeholder="Filter by ID (e.g. 1, 2, 3...)" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm w-64">
                            <button onclick="loadLogs()" class="bg-purple-600 hover:bg-purple-700 px-3 py-1.5 rounded text-sm">
                                <i class="fas fa-sync mr-1"></i> Refresh Logs
                            </button>
                            <button onclick="clearLogs()" class="bg-gray-600 hover:bg-gray-700 px-3 py-1.5 rounded text-sm">
                                <i class="fas fa-trash mr-1"></i> Clear Logs
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

                <div id="confirmModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-[100]">
                    <div class="bg-gray-800 rounded-lg p-6 w-full max-w-sm mx-4">
                        <p id="confirmModalMessage" class="text-sm sm:text-base mb-6"></p>
                        <div class="flex justify-end gap-3">
                            <button id="confirmModalCancel" class="bg-gray-600 hover:bg-gray-700 px-4 py-2 rounded text-sm min-h-[44px]">Cancel</button>
                            <button id="confirmModalOk" class="bg-red-700 hover:bg-red-600 px-4 py-2 rounded text-sm font-medium min-h-[44px]">OK</button>
                        </div>
                    </div>
                </div>
                <div id="promptModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-[100]">
                    <div class="bg-gray-800 rounded-lg p-6 w-full max-w-sm mx-4">
                        <p id="promptModalMessage" class="text-sm sm:text-base mb-4"></p>
                        <input id="promptModalInput" type="text" class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm min-h-[44px] mb-4">
                        <div class="flex justify-end gap-3">
                            <button id="promptModalCancel" class="bg-gray-600 hover:bg-gray-700 px-4 py-2 rounded text-sm min-h-[44px]">Cancel</button>
                            <button id="promptModalOk" class="bg-blue-700 hover:bg-blue-600 px-4 py-2 rounded text-sm font-medium min-h-[44px]">OK</button>
                        </div>
                    </div>
                </div>

                <div id="precheckModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50">
                    <div class="bg-gray-800 rounded-lg p-6 w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
                        <div class="flex justify-between items-center mb-4">
                            <h3 class="text-lg font-bold"><i class="fas fa-search mr-2"></i><span id="precheckTaskTitle">Pre-Check Results</span></h3>
                            <button onclick="hidePrecheckModal()" class="text-gray-400 hover:text-white"><i class="fas fa-times"></i></button>
                        </div>

                        <div id="precheckTaskInfo" class="text-sm text-gray-400 mb-3"></div>

                        <div id="precheckWarnings" class="space-y-2 mb-4"></div>

                        <div id="precheckBotCommentBox" class="mb-4 hidden">
                            <label class="block text-sm text-gray-400 mb-1"><i class="fab fa-algolia mr-1"></i>Algora Bounty Info</label>
                            <div id="precheckBotComment" class="text-xs text-gray-300 bg-gray-900 p-3 rounded font-mono whitespace-pre-wrap max-h-48 overflow-y-auto"></div>
                        </div>

                        <div class="mb-4 hidden">
                            <label class="block text-sm text-gray-400 mb-1"><i class="fas fa-book mr-1"></i>CONTRIBUTING.md Rules</label>
                            <div id="precheckContributing" class="text-xs text-gray-300 bg-gray-900 p-3 rounded font-mono whitespace-pre-wrap max-h-32 overflow-y-auto"></div>
                        </div>

                        <div class="mb-4">
                            <label class="block text-sm text-gray-400 mb-1"><i class="fas fa-comment mr-1"></i>Suggested Comment (copy & paste on GitHub)</label>
                            <textarea id="precheckComment" class="w-full h-32 bg-gray-900 text-gray-300 text-sm font-mono p-3 rounded resize-none"></textarea>
                            <div class="mt-2 flex gap-2">
                                <button id="copyCommentBtn" onclick="copyComment()" class="bg-gray-700 hover:bg-gray-600 px-3 py-1.5 rounded text-sm">
                                    <i class="fas fa-copy mr-1"></i> Copy to Clipboard
                                </button>
                                <button id="sendCommentBtn" onclick="sendComment()" class="bg-blue-700 hover:bg-blue-600 px-3 py-1.5 rounded text-sm">
                                    <i class="fas fa-paper-plane mr-1"></i> Send Comment
                                </button>
                            </div>
                        </div>

                        <div class="flex justify-end gap-3">
                            <button onclick="hidePrecheckModal()" class="bg-gray-600 hover:bg-gray-700 px-4 py-2 rounded text-sm">Cancel</button>
                            <button id="precheckProceedBtn" class="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded text-sm font-medium">
                                <i class="fas fa-play mr-1"></i> Proceed with Fix
                            </button>
                        </div>
                    </div>
                </div>

                <div id="planAttemptModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50">
                    <div class="bg-gray-800 rounded-lg p-6 w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
                        <div class="flex justify-between items-center mb-4">
                            <h3 class="text-lg font-bold"><i class="fas fa-search mr-2"></i>Plan &amp; Attempt Preview</h3>
                            <button onclick="hidePlanAttemptModal()" class="text-gray-400 hover:text-white"><i class="fas fa-times"></i></button>
                        </div>
                        <div id="planAttemptTaskInfo" class="text-sm text-gray-400 mb-3"></div>
                        <div id="planAttemptAlgoraBox" class="mb-4 hidden">
                            <details class="bg-gray-850 rounded border border-gray-700">
                                <summary class="px-3 py-2 text-sm text-cyan-400 cursor-pointer hover:bg-gray-750 rounded"><i class="fab fa-algolia mr-2"></i>Algora Bounty Info <span id="planAttemptAlgoraSummary" class="text-xs text-gray-500 ml-2"></span></summary>
                                <div id="planAttemptAlgoraComment" class="text-xs text-gray-300 bg-gray-900 p-3 font-mono whitespace-pre-wrap max-h-48 overflow-y-auto"></div>
                            </details>
                        </div>
                        <div id="planAttemptContributingBox" class="mb-4 hidden">
                            <details class="bg-gray-850 rounded border border-gray-700">
                                <summary class="px-3 py-2 text-sm text-purple-400 cursor-pointer hover:bg-gray-750 rounded"><i class="fas fa-book mr-2"></i>Owner's Rules (CONTRIBUTING.md)</summary>
                                <div id="planAttemptContributing" class="text-xs text-gray-300 bg-gray-900 p-3 font-mono whitespace-pre-wrap max-h-32 overflow-y-auto"></div>
                            </details>
                        </div>
                        <div id="planAttemptWarnings" class="space-y-2 mb-4"></div>
                        <div class="mb-4">
                            <label class="block text-sm text-gray-400 mb-1"><i class="fas fa-comment mr-1"></i>Generated /attempt Comment</label>
                            <textarea id="planAttemptComment" class="w-full h-32 bg-gray-900 text-gray-300 text-sm font-mono p-3 rounded resize-none"></textarea>
                            <div id="planAttemptGeneratedBy" class="text-xs text-gray-500 mt-1"><i class="fas fa-info-circle mr-1"></i></div>
                        </div>
                        <div class="flex justify-end gap-3">
                            <button onclick="hidePlanAttemptModal()" class="bg-gray-600 hover:bg-gray-700 px-4 py-2 rounded text-sm">Cancel</button>
                            <button id="planAttemptSendBtn" class="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded text-sm font-medium">
                                <i class="fas fa-paper-plane mr-1"></i> Send (/attempt)
                            </button>
                            <button id="planAttemptExecuteBtn" class="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded text-sm font-medium">
                                <i class="fas fa-play mr-1"></i> Process w/o Waiting
                            </button>
                        </div>
                    </div>
                </div>

                <div id="toastContainer" class="fixed top-4 right-4 z-[200] flex flex-col gap-2 max-w-sm w-full pointer-events-none"></div>

                <div id="processingModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50 p-3">
                    <div class="bg-gray-800 rounded-lg p-4 sm:p-6 w-full max-w-lg mx-auto sm:mx-4 max-h-[90vh] sm:max-h-[80vh] overflow-y-auto">
                        <div class="flex justify-between items-center mb-4">
                            <h3 class="text-base sm:text-lg font-bold"><i class="fas fa-cog fa-spin mr-2"></i>Processing Task</h3>
                            <button onclick="hideProcessingModal()" class="text-gray-400 hover:text-white p-2 min-h-[44px]"><i class="fas fa-times"></i></button>
                        </div>
                        <div class="mb-4">
                            <div class="flex items-center justify-between mb-2">
                                <span id="processingStatusBadge" class="px-3 py-1 rounded text-sm font-medium bg-blue-600">Queued</span>
                                <div class="flex items-center gap-3">
                                    <span id="processingElapsed" class="text-sm text-gray-400 font-mono">00:00</span>
                                    <span id="processingProgressText" class="text-sm text-gray-400">0%</span>
                                </div>
                            </div>
                            <div class="w-full bg-gray-700 rounded-full h-2">
                                <div id="processingProgressBar" class="bg-purple-600 h-2 rounded-full transition-all duration-300" style="width: 0%"></div>
                            </div>
                        </div>
                        <div id="processingStats" class="hidden mb-3"></div>
                        <div id="processingLogContent" class="text-sm text-gray-300 font-mono bg-gray-900 p-3 rounded h-48 sm:h-64 overflow-y-auto space-y-1"></div>
                    </div>
                </div>

                <div id="reviewDetailModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50 p-3">
                    <div class="bg-gray-800 rounded-lg w-full max-w-6xl mx-auto sm:mx-4 max-h-[90vh] flex flex-col">
                        <div class="flex justify-between items-center p-4 sm:p-6 border-b border-gray-700">
                            <h3 id="reviewDetailTitle" class="text-base sm:text-lg font-bold"><i class="fas fa-code mr-2"></i>Review Detail</h3>
                            <button onclick="closeReviewDetail()" class="text-gray-400 hover:text-white p-2 min-h-[44px]"><i class="fas fa-times"></i></button>
                        </div>
                        <div id="reviewDetailContent" class="flex-1 overflow-y-auto p-4 sm:p-6 min-h-[400px] max-h-[75vh]"></div>
                    </div>
                </div>

                <div id="taskLogsModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50 p-3">
                    <div class="bg-gray-800 rounded-lg p-4 sm:p-6 w-full max-w-4xl mx-auto sm:mx-4 max-h-[90vh] flex flex-col">
                        <div class="flex justify-between items-center mb-4">
                            <h3 id="taskLogsTitle" class="text-base sm:text-lg font-bold"><i class="fas fa-terminal mr-2"></i>Task Logs</h3>
                            <div class="flex items-center gap-2">
                                <button onclick="clearTaskLogs()" class="bg-gray-600 hover:bg-gray-700 px-3 py-1.5 rounded text-sm"><i class="fas fa-trash mr-1"></i> Clear</button>
                                <button onclick="closeTaskLogsModal()" class="text-gray-400 hover:text-white p-2 min-h-[44px]"><i class="fas fa-times"></i></button>
                            </div>
                        </div>
                        <div id="taskLogStats" class="hidden mb-3"></div>
                        <div id="taskLogsContent" class="bg-gray-900 rounded p-4 flex-1 overflow-y-auto font-mono text-sm space-y-1 min-h-[400px] max-h-[70vh]">
                            <div class="text-gray-400">Loading logs...</div>
                        </div>
                    </div>
                </div>

                <div id="settingsModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50 p-3">
                    <div class="bg-gray-800 rounded-lg p-4 sm:p-6 w-full max-w-2xl mx-auto sm:mx-4 max-h-[90vh] overflow-y-auto">
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
                                <h4 class="text-sm font-bold text-purple-400 mb-2"><i class="fas fa-users mr-1"></i> Agents</h4>
                                <div class="space-y-2">
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Dispatcher <span class="text-gray-500">(classify + decompose)</span></label>
                                        <select id="cfgRoleDispatcher" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono"></select>
                                    </div>
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Simple Coder <span class="text-gray-500">(was simple + junior)</span></label>
                                        <select id="cfgRoleSimple" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono"></select>
                                    </div>
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Super Coder <span class="text-gray-500">(unchanged)</span></label>
                                        <select id="cfgRoleSuper" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono"></select>
                                    </div>
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">CI/CD Specialist <span class="text-gray-500">(test + review)</span></label>
                                        <select id="cfgRoleCicd" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono"></select>
                                    </div>
                                </div>
                                <p class="text-xs text-gray-500 mt-2">Models are loaded from Ollama and OpenCode. Select a model for each agent role.</p>
                                <div class="grid grid-cols-2 gap-3 mt-3">
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Local Fix Cycles</label>
                                        <input type="number" id="cfgMaxLocalCycles" min="1" max="10" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                        <p class="text-xs text-gray-500 mt-0.5">CI/CD test-fix attempts before sending back to coder</p>
                                    </div>
                                    <div>
                                        <label class="block text-xs text-gray-400 mb-1">Coder Send-backs</label>
                                        <input type="number" id="cfgMaxSendBack" min="0" max="10" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                        <p class="text-xs text-gray-500 mt-0.5">Max times CI/CD sends back to coder for regeneration</p>
                                    </div>
                                </div>
                                <div class="mt-3">
                                    <label class="block text-xs text-gray-400 mb-1">Max Concurrent Tasks</label>
                                    <input type="number" id="cfgMaxConcurrentTasks" min="1" max="10" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono" style="max-width:120px">
                                    <p class="text-xs text-gray-500 mt-0.5">Number of tasks to process in parallel (1 = sequential)</p>
                                </div>
                            </div>

                            <div>
                                <h4 class="text-sm font-bold text-purple-400 mb-2"><i class="fas fa-brain mr-1"></i> Ollama</h4>
                                <div>
                                    <label class="block text-xs text-gray-400 mb-1">Base URL</label>
                                    <input type="text" id="cfgOllamaUrl" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono">
                                </div>
                            </div>

                            <div>
                                <h4 class="text-sm font-bold text-purple-400 mb-2"><i class="fas fa-cloud mr-1"></i> OpenCode GO</h4>
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
                                <p class="text-xs text-gray-500 mt-1">Configure OpenCode GO to access cloud models. API key and base URL are required.</p>
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

        <div id="startModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50 p-3">
            <div class="bg-gray-800 rounded-lg p-4 sm:p-6 w-full max-w-md mx-auto sm:mx-4 max-h-[90vh] overflow-y-auto">
                <h3 class="text-base sm:text-lg font-bold mb-4">Start Automated Mode</h3>

                <div class="space-y-4">
                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Source</label>
                        <div class="flex flex-col sm:flex-row sm:space-x-4 space-y-2 sm:space-y-0">
                            <label class="flex items-center space-x-2 cursor-pointer">
                                <input type="radio" name="startMode" value="free" id="startModeFree" checked onchange="updateStartMode()" class="accent-purple-500">
                                <span class="text-sm">Free Tasks (GitHub)</span>
                            </label>
                            <label class="flex items-center space-x-2 cursor-pointer">
                                <input type="radio" name="startMode" value="paid" id="startModePaid" onchange="updateStartMode()" class="accent-purple-500">
                                <span class="text-sm">Paid Bounties (Algora)</span>
                            </label>
                            <label class="flex items-center space-x-2 cursor-pointer">
                                <input type="radio" name="startMode" value="both" id="startModeBoth" onchange="updateStartMode()" class="accent-purple-500">
                                <span class="text-sm">Both</span>
                            </label>
                        </div>
                    </div>

                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Price Range ($)</label>
                        <div class="flex items-center space-x-2">
                            <input type="number" id="startMinPrice" value="0" min="0" placeholder="min" class="bg-gray-700 border border-gray-600 rounded px-3 py-2 w-24 text-sm min-h-[44px]">
                            <span class="text-gray-400">to</span>
                            <input type="number" id="startMaxPrice" value="0" min="0" placeholder="max" class="bg-gray-700 border border-gray-600 rounded px-3 py-2 w-24 text-sm min-h-[44px]">
                        </div>
                        <p class="text-xs text-gray-500 mt-1">0 = no filter. Existing tasks outside range will be skipped.</p>
                    </div>

                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Scan Interval (seconds)</label>
                        <input type="number" id="startInterval" value="600" min="60" max="86400" class="bg-gray-700 border border-gray-600 rounded px-3 py-2 w-24 text-sm min-h-[44px]">
                        <p class="text-xs text-gray-500 mt-1">Default 600s (10 min). Time between cycles.</p>
                    </div>
                </div>

                <div class="flex justify-end space-x-3 mt-6">
                    <button onclick="closeStartModal()" class="bg-gray-600 hover:bg-gray-700 px-4 py-2 rounded text-sm min-h-[44px]">Cancel</button>
                    <button onclick="executeStart()" id="executeStartBtn" class="bg-green-700 hover:bg-green-600 px-4 py-2 rounded text-sm min-h-[44px]">
                        <i class="fas fa-play mr-1"></i> Start
                    </button>
                </div>
            </div>
        </div>

                        <div id="scanModal" class="fixed inset-0 bg-black bg-opacity-50 hidden items-center justify-center z-50 p-3">
            <div class="bg-gray-800 rounded-lg p-4 sm:p-6 w-full max-w-md mx-auto sm:mx-4 max-h-[90vh] overflow-y-auto">
                <h3 class="text-base sm:text-lg font-bold mb-4">Scan for Tasks</h3>

                <div class="space-y-4">
                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Source</label>
                        <div class="flex flex-col sm:flex-row sm:space-x-4 space-y-2 sm:space-y-0">
                            <label class="flex items-center space-x-2 cursor-pointer">
                                <input type="radio" name="scanMode" value="test" id="scanModeTest" checked onchange="updateScanMode()" class="accent-purple-500">
                                <span class="text-sm">Free Tasks (GitHub)</span>
                            </label>
                            <label class="flex items-center space-x-2 cursor-pointer">
                                <input type="radio" name="scanMode" value="prod" id="scanModeProd" onchange="updateScanMode()" class="accent-purple-500">
                                <span class="text-sm">Paid Bounties (Algora)</span>
                            </label>
                        </div>
                    </div>

                    <div id="priceRangeSection" class="hidden">
                        <label class="block text-sm text-gray-400 mb-1">Price Range ($)</label>
                        <div class="flex items-center space-x-2">
                            <input type="number" id="minPrice" value="0" min="0" class="bg-gray-700 border border-gray-600 rounded px-3 py-2 w-24 text-sm min-h-[44px]">
                            <span class="text-gray-400">to</span>
                            <input type="number" id="maxPrice" value="0" min="0" class="bg-gray-700 border border-gray-600 rounded px-3 py-2 w-24 text-sm min-h-[44px]">
                        </div>
                        <p class="text-xs text-gray-500 mt-1">0 = no filter. Set a range to filter bounties by price.</p>
                    </div>

                    <div id="labelSelectorSection">
                        <label class="block text-sm text-gray-400 mb-1">Labels to Search</label>
                        <div id="selectedLabels" class="flex flex-wrap gap-1 mb-2"></div>
                        <div id="labelDropdownContainer">
                            <select id="labelSelector" onchange="addLabel()" class="bg-gray-700 border border-gray-600 rounded px-3 py-2 w-full text-sm min-h-[44px]">
                                <option value="">— Select a label —</option>
                                <option value="good first issue">good first issue</option>
                                <option value="help wanted">help wanted</option>
                                <option value="first-timers-only">first-timers-only</option>
                                <option value="up-for-grabs">up-for-grabs</option>
                                <option value="low hanging fruit">low hanging fruit</option>
                                <option value="bug">bug</option>
                                <option value="enhancement">enhancement</option>
                                <option value="documentation">documentation</option>
                                <option value="refactor">refactor</option>
                                <option value="needs-tests">needs-tests</option>
                                <option value="bounty">bounty</option>
                                <option value="hacktoberfest">hacktoberfest</option>
                            </select>
                        </div>
                        <p id="labelHint" class="text-xs text-gray-500 mt-1">No labels selected — will use default queries from config.</p>
                    </div>

                    <div>
                        <label class="block text-sm text-gray-400 mb-1">Max Tasks</label>
                        <input type="number" id="maxTasks" value="10" min="1" max="50" class="bg-gray-700 border border-gray-600 rounded px-3 py-2 w-24 text-sm min-h-[44px]">
                    </div>
                </div>

                <div class="flex justify-end space-x-3 mt-6">
                    <button onclick="closeScanModal()" class="bg-gray-600 hover:bg-gray-700 px-4 py-2 rounded text-sm min-h-[44px]">Cancel</button>
                    <button onclick="executeScan()" id="executeScanBtn" class="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded text-sm min-h-[44px]">
                        <i class="fas fa-search mr-1"></i> Scan
                    </button>
                </div>
            </div>
        </div>

        <script>
            let currentTab = 'new';

            function switchTab(tab) {
                currentTab = tab;
                const tabs = ['new', 'awaiting_assignment', 'processing', 'failed', 'reviews', 'rejected', 'logs'];
                tabs.forEach(t => {
                    const tabEl = document.getElementById('tab-' + t);
                    const panelEl = document.getElementById('panel-' + t);
                    if (tabEl) {
                        tabEl.className = t === tab ? 'tab-active px-2 sm:px-4 py-3 font-medium whitespace-nowrap text-xs sm:text-sm' : 'px-2 sm:px-4 py-3 font-medium text-gray-400 whitespace-nowrap text-xs sm:text-sm';
                    }
                    if (panelEl) {
                        panelEl.className = t === tab ? 'p-4' : 'p-4 hidden';
                    }
                });
                if (tab === 'reviews') loadReviews();
                if (tab === 'rejected') loadRejectedReviews();
                if (tab === 'logs') loadLogs();
                if (['new', 'awaiting_assignment', 'processing', 'failed'].includes(tab)) applyFilters();
            }

            function closeScanModal() {
                document.getElementById('scanModal').classList.add('hidden');
                document.getElementById('scanModal').classList.remove('flex');
            }

            function openScanModal() {
                window._selectedLabels = [];
                renderLabels();
                updateScanMode();
                document.getElementById('scanModal').classList.remove('hidden');
                document.getElementById('scanModal').classList.add('flex');
            }

            function updateScanMode() {
                const isTest = document.getElementById('scanModeTest').checked;
                document.getElementById('priceRangeSection').classList.toggle('hidden', isTest);
                document.getElementById('labelSelectorSection').classList.toggle('hidden', !isTest);
                if (!isTest) {
                    if (document.getElementById('minPrice').value === '0' || document.getElementById('minPrice').value === '') {
                        document.getElementById('minPrice').value = '5';
                    }
                    if (document.getElementById('maxPrice').value === '0' || document.getElementById('maxPrice').value === '') {
                        document.getElementById('maxPrice').value = '150';
                    }
                }
            }

            function addLabel() {
                const sel = document.getElementById('labelSelector');
                const val = sel.value;
                if (val && !window._selectedLabels.includes(val)) {
                    window._selectedLabels.push(val);
                    renderLabels();
                }
                sel.value = '';
            }

            function removeLabel(label) {
                window._selectedLabels = window._selectedLabels.filter(l => l !== label);
                renderLabels();
            }

            function renderLabels() {
                const container = document.getElementById('selectedLabels');
                const hint = document.getElementById('labelHint');
                const dropdown = document.getElementById('labelSelector');

                container.innerHTML = window._selectedLabels.map(l =>
                    `<span class="inline-flex items-center gap-1 bg-purple-600 text-white text-xs px-2 py-1 rounded">
                        ${l}
                        <button onclick="removeLabel('${l}')" class="hover:text-purple-200 ml-1">×</button>
                    </span>`
                ).join('');

                if (window._selectedLabels.length === 0) {
                    hint.textContent = 'No labels selected — will use default queries from config.';
                    hint.className = 'text-xs text-gray-500 mt-1';
                } else {
                    hint.textContent = `${window._selectedLabels.length} label(s) selected — will search for each.`;
                    hint.className = 'text-xs text-green-400 mt-1';
                }

                dropdown.disabled = window._selectedLabels.length >= 10;
            }

            async function executeScan() {
                const btn = document.getElementById('executeScanBtn');
                const testMode = document.getElementById('scanModeTest').checked;
                const minPrice = parseInt(document.getElementById('minPrice').value) || 0;
                const maxPrice = parseInt(document.getElementById('maxPrice').value) || 0;
                const limit = parseInt(document.getElementById('maxTasks').value) || 10;
                const labels = window._selectedLabels || [];

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
                            labels: labels.length > 0 ? labels : null
                        })
                    });
                    const data = await res.json();
                    customAlert(`Found ${data.tasks_found} tasks`);
                    loadTasks();
                    closeScanModal();
                } catch (e) {
                    customAlert('Scan failed: ' + e.message, 'error');
                } finally {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-search mr-1"></i> Scan';
                }
            }

            function animateValue(el, target, duration, suffix) {
                const start = performance.now();
                const startVal = 0;
                const endVal = target;
                const isInt = Number.isInteger(target);
                function tick(now) {
                    const p = Math.min((now - start) / duration, 1);
                    const eased = 1 - Math.pow(1 - p, 3);
                    const cur = startVal + (endVal - startVal) * eased;
                    el.textContent = (isInt ? Math.round(cur).toLocaleString() : cur.toFixed(1)) + (suffix || '');
                    if (p < 1) requestAnimationFrame(tick);
                }
                requestAnimationFrame(tick);
            }

            function escapeHtml(str) {
                const d = document.createElement('div');
                d.textContent = str;
                return d.innerHTML;
            }

            function colorForDuration(s) {
                return s < 30 ? 'tstat-good' : s < 120 ? 'tstat-warn' : 'tstat-bad';
            }

            function colorForTps(tps) {
                return tps > 15 ? 'tstat-good' : tps > 5 ? 'tstat-warn' : 'tstat-bad';
            }

            const PHASE_ORDER = ['precheck', 'dispatcher', 'coder', 'cicd', 'review'];
            const PHASE_LABELS = {precheck:'Pre-check', dispatcher:'Dispatch', coder:'Coder', cicd:'CI/CD', review:'Review'};

            function phaseFromStep(step) {
                if (!step) return 0;
                const s = step.toLowerCase();
                if (s === 'start' || s.includes('precheck')) return 0;
                if (s.includes('dispatch')) return 1;
                if (s.includes('coder') || s.includes('model') || s.includes('tokens')) return 2;
                if (s.includes('cicd') || s.includes('duration') || s.includes('timing')) return 3;
                if (s.includes('complete') || s.includes('review') || s.includes('queue')) return 4;
                return 0;
            }

            function maxTps(models) {
                let m = 0.1;
                for (const v of Object.values(models)) { if ((v.tokens_per_sec || 0) > m) m = v.tokens_per_sec; }
                return m;
            }

            function renderStatsBanner(containerId, stats, opts) {
                const container = document.getElementById(containerId);
                if (!container) return;
                const animate = opts?.animate !== false;
                const pulseClass = opts?.pulse || false;
                const dur = stats.total_duration || 0;
                const tok = stats.total_tokens || 0;
                const ptok = stats.total_prompt_tokens || 0;
                const ctok = stats.total_completion_tokens || 0;
                const models = stats.models || {};
                const step = stats.current_step || '';
                const isComplete = !stats.current_step && stats.total_duration > 0;
                const phaseIdx = isComplete ? PHASE_ORDER.length - 1 : phaseFromStep(step);
                const mTps = maxTps(models);
                const modelEntries = Object.entries(models).slice(0, 3);

                let modelHtml = '';
                for (let i = 0; i < modelEntries.length; i++) {
                    const [name, m] = modelEntries[i];
                    const tps = m.tokens_per_sec || 0;
                    const barPct = mTps > 0 ? Math.round((tps / mTps) * 100) : 0;
                    modelHtml += '<div class="stat-card"><span class="label truncate min-w-0" title="' + escapeHtml(name) + '">' + escapeHtml(name) + '</span>';
                    modelHtml += '<div class="flex items-center gap-2 min-w-0 flex-1"><div class="mini-bar flex-1 min-w-[40px]"><div class="mini-bar-fill" id="sban-bar-' + i + '" style="width:' + barPct + '%"></div></div>';
                    modelHtml += '<span class="text-xs ' + colorForTps(tps) + ' whitespace-nowrap" id="sban-tps-' + i + '">0 tok/s</span>';
                    modelHtml += '<span class="tstat-muted text-[10px] whitespace-nowrap" id="sban-mtok-' + i + '">0 tok</span></div></div>';
                }

                let html = '<div style="overflow-x: auto;" class="mb-3">';
                html += '<div class="flex gap-3' + (pulseClass ? ' pulse' : '') + '" style="min-width: 100%; width: max-content;">';
                html += '<div class="flex flex-col gap-2" style="flex: 1 1 45%; min-width: 0;">';
                html += '<div class="stat-card"><span class="label">Duration</span><span class="value ' + colorForDuration(dur) + '" id="sban-dur">0.0s</span></div>';
                if (modelHtml) {
                    html += modelHtml;
                }
                html += '</div>';
                html += '<div class="flex flex-col gap-2" style="flex: 1 1 45%; min-width: 0;">';
                html += '<div class="stat-card"><span class="label">Total Tokens</span><div class="flex items-center gap-2 min-w-0"><span class="value text-green-400" id="sban-tok">0</span><span class="tstat-muted text-[10px] whitespace-nowrap" id="sban-tok-sub">P: 0 · C: 0</span></div></div>';
                html += '<div class="stat-card"><span class="label">Phase</span><div class="flex items-center gap-2"><div class="phase-dots">';
                for (let p = 0; p < PHASE_ORDER.length; p++) {
                    let cls = 'waiting';
                    if (p < phaseIdx) cls = 'done';
                    else if (p === phaseIdx) cls = 'active';
                    html += '<span class="phase-dot ' + cls + '" title="' + PHASE_LABELS[PHASE_ORDER[p]] + '"></span>';
                }
                html += '</div><span class="tstat-muted text-[10px] whitespace-nowrap" id="sban-phase">' + (phaseIdx >= 0 ? PHASE_LABELS[PHASE_ORDER[phaseIdx]] || step : '—') + '</span></div></div>';
                html += '</div>';
                html += '</div></div>';
                container.innerHTML = html;
                container.classList.remove('hidden');

                if (animate) {
                    animateValue(document.getElementById('sban-dur'), dur, 800, 's');
                    animateValue(document.getElementById('sban-tok'), tok, 800, '');
                    document.getElementById('sban-tok-sub').textContent = 'P: ' + ptok.toLocaleString() + ' · C: ' + ctok.toLocaleString();
                    for (let i = 0; i < modelEntries.length; i++) {
                        animateValue(document.getElementById('sban-tps-' + i), modelEntries[i][1].tokens_per_sec || 0, 600, ' tok/s');
                        animateValue(document.getElementById('sban-mtok-' + i), modelEntries[i][1].total_tokens || 0, 600, ' tok');
                    }
                } else {
                    document.getElementById('sban-dur').textContent = dur.toFixed(1) + 's';
                    document.getElementById('sban-tok').textContent = tok.toLocaleString();
                    document.getElementById('sban-tok-sub').textContent = 'P: ' + ptok.toLocaleString() + ' · C: ' + ctok.toLocaleString();
                    for (let i = 0; i < modelEntries.length; i++) {
                        document.getElementById('sban-tps-' + i).textContent = (modelEntries[i][1].tokens_per_sec || 0).toFixed(1) + ' tok/s';
                        document.getElementById('sban-mtok-' + i).textContent = (modelEntries[i][1].total_tokens || 0).toLocaleString() + ' tok';
                    }
                }
            }

            let _currentTaskLogId = null;

            function viewTaskLogs(taskId) {
                _currentTaskLogId = taskId;
                const modal = document.getElementById('taskLogsModal');
                modal.classList.remove('hidden');
                modal.classList.add('flex');
                document.getElementById('taskLogsTitle').innerHTML = '<i class="fas fa-terminal mr-2"></i>Logs - Task #' + taskId;

                const statsDiv = document.getElementById('taskLogStats');
                statsDiv.classList.add('hidden');
                statsDiv.innerHTML = '';

                document.getElementById('taskLogsContent').innerHTML = '<div class="text-gray-400">Loading logs...</div>';

                fetch('/api/tasks/' + taskId + '/stats')
                    .then(r => r.json())
                    .then(stats => {
                        if (stats.total_tokens > 0 || stats.total_duration > 0) {
                            renderStatsBanner('taskLogStats', stats, { animate: true });
                        }
                    })
                    .catch(e => console.error('Failed to load stats:', e));

                fetch('/api/logs?bounty_id=' + taskId)
                    .then(res => res.json())
                    .then(logs => {
                        const container = document.getElementById('taskLogsContent');
                        if (!logs || logs.length === 0) {
                            container.innerHTML = '<div class="text-gray-400">No logs found</div>';
                            return;
                        }
                        container.innerHTML = logs.map(l => {
                            const time = l.created_at ? new Date(l.created_at).toLocaleTimeString() : '';
                            const details = l.details ? ' - ' + l.details : '';
                            return '<div class="text-xs"><span class="text-gray-500">[' + time + ']</span> <span class="text-purple-400">[' + (l.agent_type || 'system') + ']</span> <span class="text-gray-300">' + l.action + '</span><span class="text-gray-500">' + details + '</span></div>';
                        }).join('');
                        container.scrollTop = container.scrollHeight;
                    })
                    .catch(e => {
                        document.getElementById('taskLogsContent').innerHTML = '<div class="text-red-400">Failed to load logs: ' + e.message + '</div>';
                    });
            }

            function clearTaskLogs() {
                if (!_currentTaskLogId) return;
                fetch('/api/logs?bounty_id=' + _currentTaskLogId, { method: 'DELETE' })
                    .then(r => r.json())
                    .then(data => {
                        if (data.success) {
                            document.getElementById('taskLogsContent').innerHTML = '<div class="text-gray-400">Logs cleared from database</div>';
                            document.getElementById('taskLogStats').classList.add('hidden');
                        }
                    })
                    .catch(() => {});
            }

            function closeTaskLogsModal() {
                const modal = document.getElementById('taskLogsModal');
                modal.classList.add('hidden');
                modal.classList.remove('flex');
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

            function clearLogs() {
                fetch('/api/logs', { method: 'DELETE' })
                    .then(r => r.json())
                    .then(data => {
                        if (data.success) {
                            document.getElementById('logsContainer').innerHTML = '<div class="text-gray-400">All logs cleared from database</div>';
                            document.getElementById('logStats').classList.add('hidden');
                        }
                    })
                    .catch(() => {});
            }

            async function refreshStatus() {
                try {
                    const [statusRes, statsRes] = await Promise.all([
                        fetch('/api/status'),
                        fetch('/api/dashboard-stats')
                    ]);
                    const status = await statusRes.json();
                    const stats = await statsRes.json();

                    const toggleDot = document.getElementById('systemToggleDot');
                    const toggleText = document.getElementById('systemToggleText');
                    const toggleBtn = document.getElementById('systemToggle');
                    if (status.running) {
                        toggleDot.className = 'status-dot green';
                        toggleText.textContent = 'Running';
                        toggleBtn.className = 'bg-green-700 hover:bg-green-600 px-4 py-2 rounded flex items-center gap-2';
                    } else {
                        toggleDot.className = 'status-dot red';
                        toggleText.textContent = 'Start';
                        toggleBtn.className = 'bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded flex items-center gap-2';
                    }

                    if (stats.sandbox) {
                        const s = stats.sandbox;
                        const sDot = document.getElementById('sandboxToggleDot');
                        const sText = document.getElementById('sandboxToggleText');
                        const sBtn = document.getElementById('sandboxToggle');
                        if (s.enabled) {
                            sDot.className = 'status-dot green';
                            sText.textContent = 'Sandbox';
                            sBtn.className = 'bg-green-700 hover:bg-green-600 px-4 py-2 rounded flex items-center gap-2';
                        } else {
                            sDot.className = 'status-dot yellow';
                            sText.textContent = 'Sandbox Off';
                            sBtn.className = 'bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded flex items-center gap-2';
                        }
                    }

                    if (stats.uptime) {
                        document.getElementById('uptimeText').textContent = stats.uptime;
                    }

                    if (stats.system && stats.system.available !== false) {
                        const s = stats.system;
                        document.getElementById('sysCpu').textContent = s.cpu_percent.toFixed(0) + '%';
                        document.getElementById('sysRam').textContent = (s.ram_used / 1073741824).toFixed(1) + ' GB';
                        document.getElementById('sysDisk').textContent = (s.disk_used / 1073741824).toFixed(0) + ' GB';
                    }

                    if (stats.ollama_loaded) {
                        const models = stats.ollama_loaded;
                        if (models.length === 0) {
                            document.getElementById('sysOllamaModels').innerHTML = 'idle';
                        } else {
                            document.getElementById('sysOllamaModels').innerHTML = models.map(m => {
                                const parts = m.name.split(':');
                                const tag = parts[1] ? parts[1].split('-')[0] : '';
                                const displayName = parts[0] + (tag ? ':' + tag : '');
                                return '<span class="text-purple-300">' + displayName + '</span> ' +
                                       '<span class="text-gray-500">(' + m.size_gb.toFixed(1) + 'GB, ' + m.processor + ', ' + (m.context/1024).toFixed(0) + 'k ctx)</span>';
                            }).join('<br>');
                        }
                    }

                    if (stats.containers !== undefined) {
                        const c = stats.containers;
                        document.getElementById('sysContainers').textContent = c.length === 0 ? 'none' : c.length + ' running';
                    }

                    if (stats.active_agents) {
                        const agents = stats.active_agents;
                        document.getElementById('sysAgents').textContent = agents.length === 0 ? 'idle' : agents.map(a => '#' + a.task_id).join(', ');
                    }
                } catch (e) { console.error('Status refresh failed:', e); }
            }

            async function loadTasks() {
                try {
                    const res = await fetch('/api/tasks');
                    let serverTasks = await res.json();
                    const localProcessing = new Set(
                        (window.allTasks || []).filter(t => t.processing_status === 'processing').map(t => t.id)
                    );
                    window.allTasks = serverTasks.map(t => {
                        if (localProcessing.has(t.id) && t.processing_status !== 'processing') {
                            t.processing_status = 'processing';
                        }
                        return t;
                    });
                    const taskCountEl = document.getElementById('taskCount');
                    if (taskCountEl) taskCountEl.textContent = window.allTasks.length;
                    applyFilters();
                } catch (e) { console.error('Load tasks failed:', e); }
            }

            function refreshAll() { refreshStatus(); loadTasks(); loadReviews(); }

            function customConfirm(message) {
                return new Promise((resolve) => {
                    const modal = document.getElementById('confirmModal');
                    const msgEl = document.getElementById('confirmModalMessage');
                    const okBtn = document.getElementById('confirmModalOk');
                    const cancelBtn = document.getElementById('confirmModalCancel');
                    msgEl.textContent = message;
                    modal.classList.remove('hidden');
                    modal.classList.add('flex');
                    const cleanup = () => {
                        modal.classList.add('hidden');
                        modal.classList.remove('flex');
                        okBtn.onclick = null;
                        cancelBtn.onclick = null;
                    };
                    okBtn.onclick = () => { cleanup(); resolve(true); };
                    cancelBtn.onclick = () => { cleanup(); resolve(false); };
                });
            }

            function customAlert(message, type) {
                if (!type) {
                    type = message && (message.toLowerCase().includes('fail') || message.toLowerCase().includes('error')) ? 'error' : 'info';
                }
                const container = document.getElementById('toastContainer');
                const el = document.createElement('div');
                el.className = 'pointer-events-auto px-4 py-3 rounded-lg shadow-lg text-sm font-medium transition-all duration-300 ' +
                    (type === 'error' ? 'bg-red-700 text-white' : 'bg-gray-800 text-gray-100 border border-gray-600');
                el.textContent = message;
                container.appendChild(el);
                setTimeout(() => {
                    el.style.opacity = '0';
                    el.style.transform = 'translateX(100%)';
                    setTimeout(() => el.remove(), 300);
                }, 4000);
            }

            function customPrompt(message) {
                return new Promise((resolve) => {
                    const modal = document.getElementById('promptModal');
                    const msgEl = document.getElementById('promptModalMessage');
                    const inputEl = document.getElementById('promptModalInput');
                    const okBtn = document.getElementById('promptModalOk');
                    const cancelBtn = document.getElementById('promptModalCancel');
                    msgEl.textContent = message;
                    inputEl.value = '';
                    modal.classList.remove('hidden');
                    modal.classList.add('flex');
                    inputEl.focus();
                    const cleanup = () => {
                        modal.classList.add('hidden');
                        modal.classList.remove('flex');
                        okBtn.onclick = null;
                        cancelBtn.onclick = null;
                    };
                    okBtn.onclick = () => { cleanup(); resolve(inputEl.value); };
                    cancelBtn.onclick = () => { cleanup(); resolve(null); };
                });
            }

            async function toggleSystem() {
                const statusRes = await fetch('/api/status');
                const status = await statusRes.json();
                if (status.running) {
                    await fetch('/api/stop', {method: 'POST'});
                    refreshStatus();
                } else {
                    openStartModal();
                }
            }

            function updateStartMode() {
                const mode = document.querySelector('input[name="startMode"]:checked').value;
                if (mode !== 'free') {
                    const minEl = document.getElementById('startMinPrice');
                    const maxEl = document.getElementById('startMaxPrice');
                    if (!minEl.value || minEl.value === '0') minEl.value = '5';
                    if (!maxEl.value || maxEl.value === '0') maxEl.value = '150';
                }
            }

            function openStartModal() {
                // Pre-fill with current config if available
                fetch('/api/start/config').then(r => r.json()).then(cfg => {
                    if (cfg.mode) {
                        document.querySelector('input[name="startMode"][value="' + cfg.mode + '"]').checked = true;
                        document.getElementById('startMinPrice').value = cfg.min_price || 0;
                        document.getElementById('startMaxPrice').value = cfg.max_price || 0;
                        document.getElementById('startInterval').value = cfg.scan_interval || 600;
                    }
                    updateStartMode();
                }).catch(() => {
                    updateStartMode();
                });
                document.getElementById('startModal').classList.remove('hidden');
                document.getElementById('startModal').classList.add('flex');
            }

            function closeStartModal() {
                document.getElementById('startModal').classList.add('hidden');
                document.getElementById('startModal').classList.remove('flex');
            }

            async function executeStart() {
                const mode = document.querySelector('input[name="startMode"]:checked').value;
                const minPrice = parseInt(document.getElementById('startMinPrice').value) || 0;
                const maxPrice = parseInt(document.getElementById('startMaxPrice').value) || 0;
                const interval = parseInt(document.getElementById('startInterval').value) || 600;

                const btn = document.getElementById('executeStartBtn');
                btn.disabled = true;
                btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i> Starting...';

                try {
                    await fetch('/api/start', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            mode: mode,
                            min_price: minPrice,
                            max_price: maxPrice,
                            scan_interval: interval,
                        })
                    });
                    closeStartModal();
                    refreshStatus();
                } catch (e) {
                    customAlert('Failed to start: ' + e.message);
                }
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-play mr-1"></i> Start';
            }

            async function toggleSandbox() {
                const res = await fetch('/api/config');
                const cfg = await res.json();
                const current = cfg.sandbox?.enabled !== false;
                await fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ sandbox: { enabled: !current } })
                });
                refreshStatus();
            }

            async function openSettings() {
                const modal = document.getElementById('settingsModal');
                modal.classList.remove('hidden');
                modal.classList.add('flex');

                try {
                    const [cfgRes, modelsRes] = await Promise.all([
                        fetch('/api/config'),
                        fetch('/api/models')
                    ]);
                    const cfg = await cfgRes.json();
                    const modelsData = await modelsRes.json();

                    const allModels = [...(modelsData.ollama || []), ...(modelsData.opencode || [])];
                    const roleIds = ['cfgRoleDispatcher', 'cfgRoleSimple', 'cfgRoleSuper', 'cfgRoleCicd'];
                    roleIds.forEach(id => {
                        const sel = document.getElementById(id);
                        sel.innerHTML = '<option value="">— Select —</option>';
                        allModels.forEach(m => {
                            const opt = document.createElement('option');
                            opt.value = m.name;
                            opt.textContent = m.name + (m.source === 'opencode' ? ' (cloud)' : '');
                            sel.appendChild(opt);
                        });
                    });

                    document.getElementById('cfgSandboxEnabled').checked = cfg.sandbox?.enabled !== false;
                    document.getElementById('cfgOllamaUrl').value = cfg.ollama_base_url || '';

                    const roles = cfg.agents?.roles || {};
                    document.getElementById('cfgRoleDispatcher').value = roles.dispatcher || '';
                    document.getElementById('cfgRoleSimple').value = roles.simple_coder || '';
                    document.getElementById('cfgRoleSuper').value = roles.super_coder || '';
                    document.getElementById('cfgRoleCicd').value = roles.cicd_specialist || '';

                    document.getElementById('cfgMaxLocalCycles').value = cfg.agents?.max_local_fix_cycles ?? 3;
                    document.getElementById('cfgMaxSendBack').value = cfg.agents?.max_send_back ?? 2;
                    document.getElementById('cfgMaxConcurrentTasks').value = cfg.agents?.max_concurrent_tasks ?? 1;

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
                        base_url: document.getElementById('cfgOllamaUrl').value,
                    },
                    agents: {
                        roles: {
                            dispatcher: document.getElementById('cfgRoleDispatcher').value,
                            simple_coder: document.getElementById('cfgRoleSimple').value,
                            super_coder: document.getElementById('cfgRoleSuper').value,
                            cicd_specialist: document.getElementById('cfgRoleCicd').value,
                        },
                        max_local_fix_cycles: parseInt(document.getElementById('cfgMaxLocalCycles').value) || 3,
                        max_send_back: parseInt(document.getElementById('cfgMaxSendBack').value) || 2,
                        max_concurrent_tasks: parseInt(document.getElementById('cfgMaxConcurrentTasks').value) || 1,
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
                        customAlert('Settings saved successfully. Some changes may require a restart.');
                        closeSettings();
                    } else {
                        customAlert('Failed to save: ' + (result.error || 'Unknown error'));
                    }
                } catch (e) {
                    customAlert('Failed to save settings: ' + e.message);
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
                    return ['failed', 'validation_failed', 'review_failed', 'error', 'cancelled'].includes(st);
                }

                function taskGroup(t) {
                    const st = normalizeStatus(t.processing_status);
                    if (st === 'new') return 'new';
                    if (st === 'awaiting_assignment') return 'awaiting_assignment';
                    if (st === 'processing') return 'processing';
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

                const groups = { new: [], awaiting_assignment: [], processing: [], failed: [] };
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
                            case 'fetched_desc': return (b.id || 0) - (a.id || 0);
                            case 'fetched_asc': return (a.id || 0) - (b.id || 0);
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
                sortTasks(groups.failed, sortByFailed);

                // Update counts
                document.getElementById('count-new').textContent = groups.new.length;
                document.getElementById('count-awaiting_assignment').textContent = (groups.awaiting_assignment || []).length;
                document.getElementById('count-processing').textContent = groups.processing.length;
                document.getElementById('count-failed').textContent = groups.failed.length;

                // Render current tab
                const isUntouched = (t) => ['new', 'pending'].includes(normalizeStatus(t.processing_status));
                const isAwaitingAssignment = (t) => normalizeStatus(t.processing_status) === 'awaiting_assignment';
                const isProcessing = (t) => normalizeStatus(t.processing_status) === 'processing';

                function statusColor(s) {
                    const st = normalizeStatus(s);
                    if (st === 'new') return 'bg-blue-600';
                    if (st === 'awaiting_assignment') return 'bg-cyan-600';
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
                    const elapsed = _computeElapsed(t);
                    return `
                    <div class="bg-gray-750 rounded-lg p-3 sm:p-4 border border-gray-700 hover:border-purple-500 transition ${isProcessing(t) ? 'border-yellow-500' : ''}">
                        <div class="flex flex-col sm:flex-row sm:items-start gap-3">
                            <div class="flex items-start gap-3 flex-1">
                                ${showCheckbox ? `<input type="checkbox" class="task-checkbox accent-purple-500 w-4 h-4 mt-1 shrink-0" data-id="${t.id}" onchange="updateSelectedCount()" ${!isUntouched(t) ? 'disabled' : ''}>` : ''}
                                <div class="flex-1 min-w-0">
                                    <h3 class="font-bold text-sm sm:text-base">${t.title}</h3>
                                    <p class="text-gray-400 text-xs sm:text-sm">${t.repository_name || 'Unknown'}</p>
                                    <div class="flex flex-wrap gap-1.5 sm:gap-2 mt-2 text-xs">
                                        <span class="px-2 py-0.5 rounded bg-gray-600 font-mono">#${t.id}</span>
                                        <span class="px-2 py-0.5 rounded ${statusColor(t.processing_status)}">${normalizeStatus(t.processing_status) || 'new'}</span>
                                        <span class="${difficultyBadge(t.difficulty)}">${difficultyLabel(t.difficulty)}</span>
                                        ${t.is_bounty ? `<span class="px-2 py-0.5 rounded bg-amber-600 text-white">Bounty ${t.price ? '$' + t.price : 'TBD'}</span>` : `<span class="text-gray-400">$${t.price || 0}</span>`}
                                        ${t.classification ? `<span class="text-gray-400">${t.classification}</span>` : ''}
                                        <span class="text-gray-500">${formatDate(t.fetched_at)}</span>
                                        ${t.tags ? `<span class="text-gray-500 truncate max-w-[150px] sm:max-w-[200px]">${t.tags.split(',').slice(0, 3).join(', ')}</span>` : ''}
                                    </div>
                                </div>
                            </div>
                            <div class="flex flex-wrap gap-2 sm:gap-2 sm:ml-2 shrink-0 sm:flex-nowrap">
                                ${isUntouched(t) ? `<button onclick="planAttemptTask(${t.id})" class="bg-blue-600 hover:bg-blue-700 px-3 py-2 sm:py-1.5 rounded text-sm font-medium min-h-[44px] sm:min-h-0 flex-1 sm:flex-none" title="Recon + post /attempt comment, then wait for assignment"><i class="fas fa-search mr-1"></i> Plan & Attempt</button>` : ''}
                                ${isAwaitingAssignment(t) ? `<button onclick="executeTask(${t.id})" class="bg-purple-600 hover:bg-purple-700 px-3 py-2 sm:py-1.5 rounded text-sm font-medium min-h-[44px] sm:min-h-0 flex-1 sm:flex-none"><i class="fas fa-code mr-1"></i> Execute</button>` : ''}
                                ${isAwaitingAssignment(t) ? `<button onclick="resetTask(${t.id})" class="bg-gray-600 hover:bg-gray-700 px-3 py-2 sm:py-1.5 rounded text-sm min-h-[44px] sm:min-h-0" title="Reset to New"><i class="fas fa-undo"></i></button>` : ''}
                                ${isProcessing(t) ? `<span class="inline-flex items-center gap-2 px-3 py-2 sm:py-1.5 rounded bg-yellow-600/50 text-yellow-300 text-sm font-mono"><i class="fas fa-spinner fa-spin"></i> <span id="elapsed_${t.id}" class="tabular-nums">${elapsed}</span></span>` : ''}
                                ${isProcessing(t) ? `<button onclick="killTask(${t.id})" class="bg-red-700 hover:bg-red-600 px-3 py-2 sm:py-1.5 rounded text-sm min-h-[44px] sm:min-h-0 font-medium" title="Kill task immediately and release resources"><i class="fas fa-stop-circle mr-1"></i> Kill</button>` : ''}
                                ${isProcessing(t) ? `<button onclick="resetTask(${t.id})" class="bg-gray-600 hover:bg-gray-700 px-3 py-2 sm:py-1.5 rounded text-sm min-h-[44px] sm:min-h-0" title="Reset to New"><i class="fas fa-undo"></i></button>` : ''}
                                ${isFailed(t) ? `<button onclick="retryTask(${t.id})" class="bg-blue-600 hover:bg-blue-700 px-3 py-2 sm:py-1.5 rounded text-sm font-medium min-h-[44px] sm:min-h-0 flex-1 sm:flex-none"><i class="fas fa-redo mr-1"></i> Retry</button>` : ''}
                                ${isFailed(t) ? `<button onclick="deleteFailedTask(${t.id})" class="bg-red-600 hover:bg-red-700 px-3 py-2 sm:py-1.5 rounded text-sm min-h-[44px] sm:min-h-0" title="Delete Task"><i class="fas fa-trash mr-1"></i> Delete</button>` : ''}
                                ${!isUntouched(t) && !isAwaitingAssignment(t) && !isFailed(t) ? `<button onclick="deleteTaskWorkspace(${t.id})" class="bg-gray-600 hover:bg-gray-700 px-3 py-2 sm:py-1.5 rounded text-sm min-h-[44px] sm:min-h-0" title="Delete Local Files"><i class="fas fa-trash"></i></button>` : ''}
                                <button onclick="viewTaskLogs(${t.id})" class="bg-gray-600 hover:bg-gray-700 px-3 py-2 sm:py-1.5 rounded text-sm min-h-[44px] sm:min-h-0" title="View Logs"><i class="fas fa-terminal"></i></button>
                                <a href="${t.issue_url}" target="_blank" class="bg-gray-600 hover:bg-gray-700 px-3 py-2 sm:py-1.5 rounded text-sm min-h-[44px] sm:min-h-0"><i class="fas fa-external-link"></i></a>
                            </div>
                        </div>
                    </div>`;
                }

                function _computeElapsed(t) {
                    const start = t.started_at || t.processing_started_at;
                    if (!start) return '00:00';
                    const diff = Math.floor((Date.now() - new Date(start).getTime()) / 1000);
                    if (diff < 0) return '00:00';
                    const m = Math.floor(diff / 60);
                    const s = diff % 60;
                    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
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
                renderList('awaitingAssignmentList', groups.awaiting_assignment || [], false);
                renderList('processingList', groups.processing, false);
                renderList('failedList', groups.failed, false);

                const filteredCount = groups.new.length;
                const totalCount = window.allTasks.filter(t => taskGroup(t) === 'new').length;
                document.getElementById('filteredCount').textContent = filteredCount !== totalCount ? `${filteredCount} of ${totalCount}` : '';
                updateSelectedCount();
            }

            async function loadReviews() {
                try {
                    const res = await fetch('/api/reviews?status=pending');
                    let reviews = await res.json();
                    const container = document.getElementById('reviewsList');
                    if (reviews.length === 0) { container.innerHTML = '<div class="text-gray-400">No pending reviews</div>'; return; }
                    
                    document.getElementById('count-reviews').textContent = reviews.length;
                    
                    window._reviewsData = {};
                    
                    // Sort reviews
                    const sortBy = document.getElementById('sortByReviews').value;
                    reviews.sort((a, b) => {
                        const aFinished = new Date(a.reviewed_at || a.created_at || 0);
                        const bFinished = new Date(b.reviewed_at || b.created_at || 0);
                        const aCreated = new Date(a.created_at || 0);
                        const bCreated = new Date(b.created_at || 0);
                        
                        switch (sortBy) {
                            case 'finished_desc': return bFinished - aFinished;
                            case 'finished_asc': return aFinished - bFinished;
                            case 'created_desc': return bCreated - aCreated;
                            case 'created_asc': return aCreated - bCreated;
                            default: return 0;
                        }
                    });
                    
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
                                <button onclick="approveReview(${r.id})" title="Approve fix, create GitHub PR from branch, then delete local workspace" class="bg-green-600 hover:bg-green-700 px-3 py-1.5 rounded text-sm">Approve & PR</button>
                                <button onclick="rejectReview(${r.id})" title="Reject fix, post failure comment on issue, then delete local workspace" class="bg-red-600 hover:bg-red-700 px-3 py-1.5 rounded text-sm">Reject</button>
                                <button onclick="trashReview(${r.id}, ${taskId})" title="Delete task and review entry permanently" class="bg-gray-600 hover:bg-gray-700 px-3 py-1.5 rounded text-sm"><i class="fas fa-trash mr-1"></i> Trash</button>
                                ${issueUrl ? `<a href="${issueUrl}" target="_blank" class="bg-gray-600 hover:bg-gray-700 px-3 py-1.5 rounded text-sm"><i class="fas fa-external-link mr-1"></i>Issue</a>` : ''}
                            </div>
                        `;
                        container.appendChild(div);
                    });
                } catch (e) { console.error('Load reviews failed:', e); }
            }

            async function loadRejectedReviews() {
                try {
                    const res = await fetch('/api/reviews?status=rejected');
                    let reviews = await res.json();
                    const container = document.getElementById('rejectedList');
                    document.getElementById('count-rejected').textContent = reviews.length;
                    if (reviews.length === 0) { container.innerHTML = '<div class="text-gray-400">No rejected reviews</div>'; return; }

                    const sortBy = document.getElementById('sortByRejected').value;
                    reviews.sort((a, b) => {
                        const aCreated = new Date(a.created_at || 0);
                        const bCreated = new Date(b.created_at || 0);
                        const aFinished = new Date(a.reviewed_at || 0);
                        const bFinished = new Date(b.reviewed_at || 0);
                        switch (sortBy) {
                            case 'finished_desc': return bFinished - aFinished;
                            case 'finished_asc': return aFinished - bFinished;
                            case 'created_desc': return bCreated - aCreated;
                            case 'created_asc': return aCreated - bCreated;
                            default: return bFinished - aFinished;
                        }
                    });

                    window._reviewsData = window._reviewsData || {};
                    container.innerHTML = '';
                    reviews.forEach(r => {
                        window._reviewsData[r.id] = r;
                        const title = (r.title || 'Untitled').replace(/</g, '&lt;');
                        const repo = (r.repository_name || 'Unknown repo').replace(/</g, '&lt;');
                        const agent = (r.agent_type || 'unknown').replace(/</g, '&lt;');
                        const taskId = r.bounty_id || r.id;
                        const reason = (r.reviewer_comments || '').replace(/</g, '&lt;');

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
                                        ${reason ? `<span class="text-red-400">Reason: ${reason}</span>` : ''}
                                    </div>
                                </div>
                            </div>
                            <div class="mt-3 flex flex-wrap gap-2">
                                <button onclick="showReviewDiff(${r.id})" class="text-sm text-purple-400 hover:text-purple-300 bg-gray-700 px-3 py-1.5 rounded"><i class="fas fa-code mr-1"></i> View Diff</button>
                                ${r.review_notes ? `<button onclick="showReviewComment(${r.id})" class="text-sm text-purple-400 hover:text-purple-300 bg-gray-700 px-3 py-1.5 rounded"><i class="fas fa-comment mr-1"></i> View Comment</button>` : ''}
                                <button onclick="viewTaskLogs(${taskId})" class="text-sm text-gray-400 hover:text-gray-300 bg-gray-700 px-3 py-1.5 rounded"><i class="fas fa-terminal mr-1"></i> Logs</button>
                                <button onclick="retryRejected(${r.id}, ${taskId})" class="bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded text-sm"><i class="fas fa-redo mr-1"></i> Retry</button>
                                <button onclick="deleteRejected(${r.id}, ${taskId})" class="bg-red-600 hover:bg-red-700 px-3 py-1.5 rounded text-sm"><i class="fas fa-trash mr-1"></i> Delete</button>
                            </div>
                        `;
                        container.appendChild(div);
                    });
                } catch (e) { console.error('Load rejected reviews failed:', e); }
            }

            async function retryRejected(reviewId, taskId) {
                if (!await customConfirm('Retry this task? It will be reset and processed again.')) return;
                try {
                    await fetch('/api/reviews/' + reviewId + '/retry', { method: 'POST' });
                    loadRejectedReviews();
                    refreshAll();
                } catch (e) { customAlert('Retry failed: ' + e.message); }
            }

            async function deleteRejected(reviewId, taskId) {
                if (!await customConfirm('Delete this task permanently?')) return;
                try {
                    await fetch('/api/tasks/' + taskId + '/delete', { method: 'DELETE' });
                    // Also clean up the review queue entry
                    await fetch('/api/reviews/' + reviewId + '/delete', { method: 'DELETE' });
                    loadRejectedReviews();
                    refreshAll();
                } catch (e) { customAlert('Delete failed: ' + e.message); }
            }

            async function retryAllRejected() {
                const container = document.getElementById('rejectedList');
                const items = container.querySelectorAll('[data-review-id]');
                if (!await customConfirm('Retry all rejected tasks?')) return;
                try {
                    const res = await fetch('/api/reviews?status=rejected');
                    const reviews = await res.json();
                    for (const r of reviews) {
                        await fetch('/api/reviews/' + r.id + '/retry', { method: 'POST' });
                    }
                    loadRejectedReviews();
                    refreshAll();
                } catch (e) { customAlert('Retry all failed: ' + e.message); }
            }

            async function deleteAllRejected() {
                if (!await customConfirm('Delete all rejected tasks permanently?')) return;
                try {
                    const res = await fetch('/api/reviews?status=rejected');
                    const reviews = await res.json();
                    for (const r of reviews) {
                        await fetch('/api/tasks/' + (r.bounty_id || r.id) + '/delete', { method: 'DELETE' });
                        await fetch('/api/reviews/' + r.id + '/delete', { method: 'DELETE' });
                    }
                    loadRejectedReviews();
                    refreshAll();
                } catch (e) { customAlert('Delete all failed: ' + e.message); }
            }

            function showReviewDiff(id) {
                const r = window._reviewsData[id];
                if (!r) return;
                document.getElementById('reviewDetailTitle').innerHTML = '<i class="fas fa-code mr-2"></i>Code Diff - ' + (r.title || '');
                const container = document.getElementById('reviewDetailContent');
                container.innerHTML = renderDiff(r.diff_content || 'No diff available');
                const modal = document.getElementById('reviewDetailModal');
                modal.classList.remove('hidden');
                modal.classList.add('flex');
            }

            function renderDiff(diffText) {
                if (!diffText || diffText === 'No diff available') {
                    return '<div class="text-gray-400 text-center py-8">' + diffText + '</div>';
                }

                const files = parseDiffFiles(diffText);
                let html = '<div class="space-y-4">';
                html += '<div class="flex items-center justify-between text-sm text-gray-400 mb-2">';
                html += '<span>' + files.length + ' file(s) changed</span>';
                html += '<div class="flex gap-3"><span class="flex items-center gap-1"><span class="w-3 h-3 bg-green-900/60 border border-green-700 inline-block"></span> Added</span>';
                html += '<span class="flex items-center gap-1"><span class="w-3 h-3 bg-red-900/60 border border-red-700 inline-block"></span> Removed</span></div>';
                html += '</div>';

                files.forEach((file, idx) => {
                    const addedCount = file.lines.filter(l => l.type === 'add').length;
                    const removedCount = file.lines.filter(l => l.type === 'remove').length;
                    html += '<div class="border border-gray-700 rounded-lg overflow-hidden">';
                    html += '<div class="bg-gray-750 px-4 py-2 flex items-center justify-between cursor-pointer hover:bg-gray-700" onclick="toggleDiffFile(' + idx + ')">';
                    html += '<div class="flex items-center gap-3">';
                    html += '<i id="fileArrow' + idx + '" class="fas fa-chevron-down text-gray-400 transition-transform"></i>';
                    html += '<i class="fas fa-file-code text-gray-500"></i>';
                    html += '<span class="font-mono text-sm text-gray-300">' + escapeHtml(file.path) + '</span>';
                    html += '</div>';
                    html += '<div class="flex items-center gap-3 text-xs">';
                    if (addedCount > 0) html += '<span class="text-green-400">+' + addedCount + '</span>';
                    if (removedCount > 0) html += '<span class="text-red-400">-' + removedCount + '</span>';
                    html += '</div></div>';

                    html += '<div id="fileContent' + idx + '" class="overflow-x-auto">';
                    html += '<table class="w-full text-xs font-mono">';
                    html += '<tbody>';

                    let lineNum = 0;
                    file.lines.forEach(line => {
                        if (line.type === 'context' || line.type === 'add' || line.type === 'remove') {
                            lineNum++;
                            const rowClass = line.type === 'add' ? 'bg-green-900/30' : line.type === 'remove' ? 'bg-red-900/30' : '';
                            const numColor = line.type === 'add' ? 'text-green-600' : line.type === 'remove' ? 'text-red-600' : 'text-gray-600';
                            const prefix = line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' ';
                            const prefixColor = line.type === 'add' ? 'text-green-400' : line.type === 'remove' ? 'text-red-400' : 'text-gray-500';
                            html += '<tr class="' + rowClass + '">';
                            html += '<td class="select-none text-right pr-3 pl-3 ' + numColor + ' w-12 border-r border-gray-700/50">' + lineNum + '</td>';
                            html += '<td class="pl-2 pr-4 py-0.5 ' + prefixColor + ' select-none w-4">' + prefix + '</td>';
                            html += '<td class="pl-2 py-0.5 whitespace-pre">' + highlightCode(escapeHtml(line.content)) + '</td>';
                            html += '</tr>';
                        } else if (line.type === 'hunk') {
                            html += '<tr class="bg-gray-750">';
                            html += '<td colspan="3" class="px-3 py-1 text-gray-400 select-none">' + escapeHtml(line.content) + '</td>';
                            html += '</tr>';
                        } else if (line.type === 'header') {
                            html += '<tr class="bg-gray-750">';
                            html += '<td colspan="3" class="px-3 py-1 text-purple-400 select-none font-bold">' + escapeHtml(line.content) + '</td>';
                            html += '</tr>';
                        }
                    });

                    html += '</tbody></table></div></div>';
                });

                html += '</div>';
                return html;
            }

            function parseDiffFiles(diffText) {
                const files = [];
                let currentFile = null;
                const lines = diffText.split('\\n');

                for (let i = 0; i < lines.length; i++) {
                    const line = lines[i];

                    if (line.startsWith('diff --git ')) {
                        if (currentFile) files.push(currentFile);
                        const match = line.match(/diff --git a\/(.*) b\/(.*)/);
                        currentFile = {
                            path: match ? match[2] : 'unknown',
                            lines: []
                        };
                        currentFile.lines.push({ type: 'header', content: line });
                    } else if (line.startsWith('--- ') || line.startsWith('+++ ')) {
                        if (currentFile) {
                            currentFile.lines.push({ type: 'header', content: line });
                        }
                    } else if (line.startsWith('@@')) {
                        if (currentFile) {
                            currentFile.lines.push({ type: 'hunk', content: line });
                        }
                    } else if (line.startsWith('+')) {
                        if (currentFile) {
                            currentFile.lines.push({ type: 'add', content: line.substring(1) });
                        }
                    } else if (line.startsWith('-')) {
                        if (currentFile) {
                            currentFile.lines.push({ type: 'remove', content: line.substring(1) });
                        }
                    } else {
                        if (currentFile) {
                            currentFile.lines.push({ type: 'context', content: line });
                        }
                    }
                }

                if (currentFile) files.push(currentFile);
                return files;
            }

            function toggleDiffFile(idx) {
                const content = document.getElementById('fileContent' + idx);
                const arrow = document.getElementById('fileArrow' + idx);
                if (content.style.display === 'none') {
                    content.style.display = '';
                    arrow.style.transform = '';
                } else {
                    content.style.display = 'none';
                    arrow.style.transform = 'rotate(-90deg)';
                }
            }

            function escapeHtml(text) {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }

            function highlightCode(text) {
                return text
                    .replace(/\b(function|const|let|var|return|if|else|for|while|class|import|from|export|default|async|await|try|catch|new|this|self|def|print)\b/g, '<span class="text-purple-400">$1</span>')
                    .replace(/\b(true|false|null|None|True|False|undefined|NaN)\b/g, '<span class="text-orange-400">$1</span>')
                    .replace(/\b(\d+\.?\d*)\b/g, '<span class="text-cyan-400">$1</span>')
                    .replace(/(["'])(.*?)\1/g, '<span class="text-green-400">$1$2$1</span>')
                    .replace(/(\/\/.*$|#.*$)/gm, '<span class="text-gray-500">$1</span>');
            }

            function showReviewComment(id) {
                const r = window._reviewsData[id];
                if (!r) return;
                document.getElementById('reviewDetailTitle').innerHTML = '<i class="fas fa-comment mr-2"></i>Suggested Comment - ' + (r.title || '');
                const container = document.getElementById('reviewDetailContent');
                container.innerHTML = '<div class="bg-gray-900 rounded-lg p-4 font-mono text-sm whitespace-pre-wrap text-gray-300">' + escapeHtml(r.review_notes || 'No comment available') + '</div>';
                const modal = document.getElementById('reviewDetailModal');
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
                    if (!d.success) customAlert('Failed to open: ' + d.error);
                });
            }

            async function planAttemptTask(id) {
                try {
                    const res = await fetch('/api/tasks/' + id + '/plan-attempt-preview');
                    const data = await res.json();
                    showPlanAttemptModal(id, data);
                } catch (e) {
                    customAlert('Plan & Attempt error: ' + e.message);
                }
            }

            function showPlanAttemptModal(taskId, data) {
                const modal = document.getElementById('planAttemptModal');
                modal.classList.remove('hidden');
                modal.classList.add('flex');
                const task = window.allTasks.find(t => t.id === taskId);
                const infoEl = document.getElementById('planAttemptTaskInfo');
                if (task) {
                    infoEl.innerHTML = '<span class="px-2 py-0.5 rounded bg-gray-600 font-mono text-xs mr-2">#' + task.id + '</span><span class="mr-3">' + (task.repository_name || 'Unknown') + '</span>' + (task.price ? '<span class="text-amber-400">$' + task.price + '</span>' : '');
                } else {
                    infoEl.innerHTML = 'Task #' + taskId;
                }
                const algoraBox = document.getElementById('planAttemptAlgoraBox');
                const algoraEl = document.getElementById('planAttemptAlgoraComment');
                const algoraSummary = document.getElementById('planAttemptAlgoraSummary');
                if (data.algora_bot_comment) {
                    algoraBox.classList.remove('hidden');
                    algoraEl.textContent = data.algora_bot_comment;
                    var wip = data.algora_wip_count || 0;
                    var awardTotal = data.algora_award_total || 0;
                    var awardCount = data.algora_award_count || 0;
                    var summaryParts = [];
                    if (wip > 0) summaryParts.push('WIP: ' + wip);
                    if (awardCount > 0) summaryParts.push('Awards: $' + awardTotal + ' (' + awardCount + ' entries)');
                    algoraSummary.textContent = summaryParts.length > 0 ? '\u2014 ' + summaryParts.join(' | ') : '';
                } else {
                    algoraBox.classList.add('hidden');
                }
                const contribBox = document.getElementById('planAttemptContributingBox');
                const contribEl = document.getElementById('planAttemptContributing');
                if (data.contributing_rules) {
                    contribBox.classList.remove('hidden');
                    contribEl.textContent = data.contributing_rules;
                } else {
                    contribBox.classList.add('hidden');
                }
                const warningsContainer = document.getElementById('planAttemptWarnings');
                warningsContainer.innerHTML = '';
                if (data.error) {
                    const div = document.createElement('div');
                    div.className = 'text-sm text-red-400 flex items-center gap-2';
                    div.innerHTML = '<i class="fas fa-times-circle"></i> Pre-check error: ' + data.error;
                    warningsContainer.appendChild(div);
                } else {
                    let hasIssues = false;
                    if (data.is_assigned && data.assignees && data.assignees.length > 0) {
                        hasIssues = true;
                        const div = document.createElement('div');
                        div.className = 'text-sm text-red-400 flex items-center gap-2';
                        div.innerHTML = '<i class="fas fa-user-lock"></i> Assigned to: ' + data.assignees.join(', ');
                        warningsContainer.appendChild(div);
                    }
                    if (data.recent_claims && data.recent_claims.length > 0) {
                        hasIssues = true;
                        data.recent_claims.forEach(function(c) {
                            const div = document.createElement('div');
                            div.className = 'text-sm text-orange-400 flex items-center gap-2';
                            div.innerHTML = '<i class="fas fa-hand-paper"></i> @' + c.user + ' claimed ' + c.time;
                            warningsContainer.appendChild(div);
                        });
                    }
                    if (data.algora_status === 'locked') {
                        hasIssues = true;
                        const div = document.createElement('div');
                        div.className = 'text-sm text-red-400 flex items-center gap-2';
                        div.innerHTML = '<i class="fas fa-lock"></i> Algora exclusive bounty assigned to @' + (data.algora_assignee || 'unknown');
                        warningsContainer.appendChild(div);
                    }
                    if (data.winning_prs && data.winning_prs.length > 0) {
                        hasIssues = true;
                        data.winning_prs.forEach(function(p) {
                            const div = document.createElement('div');
                            div.className = 'text-sm text-red-400 flex items-center gap-2';
                            div.innerHTML = '<i class="fas fa-code-branch"></i> PR #' + p.number + ' by @' + p.user + ' already passing CI';
                            warningsContainer.appendChild(div);
                        });
                    }
                    if (data.active_prs && data.active_prs.length > 0 && (!data.winning_prs || data.winning_prs.length === 0)) {
                        hasIssues = true;
                        data.active_prs.forEach(function(p) {
                            const div = document.createElement('div');
                            div.className = 'text-sm text-yellow-400 flex items-center gap-2';
                            div.innerHTML = '<i class="fas fa-code-branch"></i> PR #' + p.number + ' by @' + p.user + ' \u2014 CI status: ' + (p.ci_passing ? 'passing' : 'pending/failing');
                            warningsContainer.appendChild(div);
                        });
                    }
                    if (data.warnings && data.warnings.length > 0) {
                        data.warnings.forEach(function(w) {
                            const div = document.createElement('div');
                            div.className = 'text-sm text-yellow-400 flex items-center gap-2';
                            div.innerHTML = '<i class="fas fa-exclamation-triangle"></i> ' + w;
                            warningsContainer.appendChild(div);
                        });
                    }
                    if (!hasIssues && (!data.warnings || data.warnings.length === 0)) {
                        var d = document.createElement('div');
                        d.className = 'text-sm text-green-400 flex items-center gap-2';
                        d.innerHTML = '<i class="fas fa-check-circle mr-1"></i> Issue appears available \u2014 no conflicts detected';
                        warningsContainer.appendChild(d);
                    }

                    var wip = data.algora_wip_count;
                    var awardCount = data.algora_award_count;
                    var hasAlgora = !!(data.algora_bot_comment);
                    var wipTxt = (hasAlgora && wip !== undefined && wip !== null) ? String(wip) : 'not detected';
                    var awardTxt = (hasAlgora && awardCount !== undefined && awardCount !== null) ? String(awardCount) : 'not detected';
                    var d = document.createElement('div');
                    d.className = 'text-sm text-green-400 flex items-center gap-2 border-t border-gray-700 pt-2 mt-2 font-medium';
                    d.innerHTML = '<i class="fab fa-algolia"></i> WIP count: ' + wipTxt + ', Awards: ' + awardTxt + ' <span id="planAttemptDebugToggle" class="text-xs text-gray-600 cursor-pointer hover:text-gray-400">[debug]</span>';
                    warningsContainer.appendChild(d);
                    document.getElementById('planAttemptDebugToggle').onclick = function() {
                        var x = document.getElementById('planAttemptDebugBot');
                        x.style.display = (x.style.display === 'none') ? 'block' : 'none';
                    };
                    var debugDiv = document.createElement('div');
                    debugDiv.id = 'planAttemptDebugBot';
                    debugDiv.className = 'text-xs text-gray-600 font-mono whitespace-pre-wrap max-h-32 overflow-y-auto bg-gray-900 p-2 rounded hidden';
                    debugDiv.textContent = data._debug_raw_bot || '(no raw bot data)';
                    warningsContainer.appendChild(debugDiv);
                }
                document.getElementById('planAttemptComment').value = data.generated_comment || '';
                document.getElementById('planAttemptGeneratedBy').innerHTML = '<i class="fas fa-info-circle mr-1"></i>' + (data.generated_by || 'Generated by Bounty Factory');
                document.getElementById('planAttemptSendBtn').onclick = async function() {
                    const body = document.getElementById('planAttemptComment').value;
                    if (!body.trim()) { customAlert('Comment cannot be empty'); return; }
                    if (!await customConfirm('Post this /attempt comment on GitHub and wait for maintainer assignment?')) return;
                    const btn = document.getElementById('planAttemptSendBtn');
                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i> Sending...';
                    try {
                        const res = await fetch('/api/tasks/' + taskId + '/submit-attempt', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({body: body, execute: false}),
                        });
                        const result = await res.json();
                        if (result.success) {
                            customAlert('/attempt posted! Waiting for maintainer assignment before execution.');
                            hidePlanAttemptModal();
                            loadTasks();
                        } else {
                            customAlert('Failed: ' + (result.error || 'Unknown error'));
                            btn.disabled = false;
                            btn.innerHTML = '<i class="fas fa-paper-plane mr-1"></i> Send (/attempt)';
                        }
                    } catch (e) {
                        customAlert('Error: ' + e.message);
                        btn.disabled = false;
                        btn.innerHTML = '<i class="fas fa-paper-plane mr-1"></i> Send (/attempt)';
                    }
                };
                document.getElementById('planAttemptExecuteBtn').onclick = async function() {
                    if (!await customConfirm('Start execution immediately without posting /attempt comment? (Manual test mode)')) return;
                    hidePlanAttemptModal();
                    if (task) task.processing_status = 'processing';
                    applyFilters();
                    showProcessingModal(taskId);
                    try {
                        await fetch('/api/tasks/' + taskId + '/submit-attempt', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({execute: true}),
                        });
                        loadTasks();
                    } catch (e) {
                        customAlert('Execute error: ' + e.message);
                        loadTasks();
                    }
                };
            }

            function hidePlanAttemptModal() {
                const modal = document.getElementById('planAttemptModal');
                modal.classList.add('hidden');
                modal.classList.remove('flex');
            }

            async function executeTask(id) {
                if (!await customConfirm('Execute coding phase?\\n\\nThis will clone the repo, generate a fix, run tests, and create a PR.')) return;
                const task = window.allTasks.find(t => t.id === id);
                if (task) task.processing_status = 'processing';
                applyFilters();
                showProcessingModal(id);
                try {
                    const res = await fetch('/api/tasks/' + id + '/execute', { method: 'POST' });
                    const data = await res.json();
                    loadTasks();
                } catch (e) {
                    customAlert('Execute error: ' + e.message);
                    loadTasks();
                }
            }

            async function processTask(id) {
                const precheck = await fetch('/api/tasks/' + id + '/precheck').then(r => r.json());
                showPrecheckModal(id, precheck);
            }

            function showPrecheckModal(taskId, precheck) {
                _precheckTaskId = taskId;
                const modal = document.getElementById('precheckModal');
                modal.classList.remove('hidden');
                modal.classList.add('flex');

                const task = window.allTasks.find(t => t.id === taskId);
                const titleEl = document.getElementById('precheckTaskTitle');
                const infoEl = document.getElementById('precheckTaskInfo');

                if (task) {
                    titleEl.textContent = (task.title || 'Task').replace(/</g, '&lt;');
                    infoEl.innerHTML = `
                        <span class="px-2 py-0.5 rounded bg-gray-600 font-mono text-xs mr-2">#${task.id}</span>
                        <span class="mr-3">${task.repository_name || 'Unknown'}</span>
                        ${task.price ? `<span class="text-amber-400">$${task.price}</span>` : ''}
                    `;
                } else {
                    titleEl.textContent = 'Pre-Check Results';
                    infoEl.innerHTML = '';
                }

                const warningsContainer = document.getElementById('precheckWarnings');
                const commentBox = document.getElementById('precheckComment');
                const contributingBox = document.getElementById('precheckContributing');
                const proceedBtn = document.getElementById('precheckProceedBtn');

                warningsContainer.innerHTML = '';

                if (precheck.error) {
                    const div = document.createElement('div');
                    div.className = 'text-sm text-red-400 flex items-center gap-2';
                    div.innerHTML = `<i class="fas fa-times-circle"></i> Pre-check error: ${precheck.error}`;
                    warningsContainer.appendChild(div);
                    proceedBtn.onclick = () => {
                        hidePrecheckModal();
                        proceedToProcess(taskId);
                    };
                } else {
                    let hasIssues = false;

                    if (precheck.is_assigned && precheck.assignees.length > 0) {
                        hasIssues = true;
                        const div = document.createElement('div');
                        div.className = 'text-sm text-red-400 flex items-center gap-2';
                        div.innerHTML = `<i class="fas fa-user-lock"></i> Assigned to: ${precheck.assignees.join(', ')}`;
                        warningsContainer.appendChild(div);
                    }

                    if (precheck.recent_claims && precheck.recent_claims.length > 0) {
                        hasIssues = true;
                        precheck.recent_claims.forEach(c => {
                            const div = document.createElement('div');
                            div.className = 'text-sm text-orange-400 flex items-center gap-2';
                            div.innerHTML = `<i class="fas fa-hand-paper"></i> @${c.user} claimed ${c.time}`;
                            warningsContainer.appendChild(div);
                        });
                    }

                    if (precheck.algora_status === 'locked') {
                        hasIssues = true;
                        const div = document.createElement('div');
                        div.className = 'text-sm text-red-400 flex items-center gap-2';
                        div.innerHTML = `<i class="fas fa-lock"></i> Algora exclusive bounty assigned to @${precheck.algora_assignee || 'unknown'}`;
                        warningsContainer.appendChild(div);
                    }

                    if (precheck.winning_prs && precheck.winning_prs.length > 0) {
                        hasIssues = true;
                        precheck.winning_prs.forEach(p => {
                            const div = document.createElement('div');
                            div.className = 'text-sm text-red-400 flex items-center gap-2';
                            div.innerHTML = `<i class="fas fa-code-branch"></i> PR #${p.number} by @${p.user} already passing CI`;
                            warningsContainer.appendChild(div);
                        });
                    }

                    if (precheck.active_prs && precheck.active_prs.length > 0 && (!precheck.winning_prs || precheck.winning_prs.length === 0)) {
                        hasIssues = true;
                        precheck.active_prs.forEach(p => {
                            const div = document.createElement('div');
                            div.className = 'text-sm text-yellow-400 flex items-center gap-2';
                            div.innerHTML = `<i class="fas fa-code-branch"></i> PR #${p.number} by @${p.user} — CI status: ${p.ci_passing ? 'passing' : 'pending/failing'}`;
                            warningsContainer.appendChild(div);
                        });
                    }

                    if (precheck.warnings && precheck.warnings.length > 0) {
                        precheck.warnings.forEach(w => {
                            const div = document.createElement('div');
                            div.className = 'text-sm text-yellow-400 flex items-center gap-2';
                            div.innerHTML = `<i class="fas fa-exclamation-triangle"></i> ${w}`;
                            warningsContainer.appendChild(div);
                        });
                    }

                    if (!hasIssues && (!precheck.warnings || precheck.warnings.length === 0)) {
                        warningsContainer.innerHTML = '<div class="text-sm text-green-400"><i class="fas fa-check-circle mr-1"></i> Issue appears available - no conflicts detected</div>';
                    }

                    proceedBtn.onclick = () => {
                        hidePrecheckModal();
                        proceedToProcess(taskId);
                    };
                }

                commentBox.value = precheck.suggested_comment || '';

                const botCommentBox = document.getElementById('precheckBotCommentBox');
                const botCommentEl = document.getElementById('precheckBotComment');
                if (precheck.algora_bot_comment) {
                    botCommentBox.classList.remove('hidden');
                    botCommentEl.textContent = precheck.algora_bot_comment;
                } else {
                    botCommentBox.classList.add('hidden');
                }

                if (precheck.contributing_rules) {
                    contributingBox.parentElement.classList.remove('hidden');
                    contributingBox.textContent = precheck.contributing_rules;
                } else {
                    contributingBox.parentElement.classList.add('hidden');
                }
            }

            function proceedToProcess(taskId) {
                const task = window.allTasks.find(t => t.id === taskId);
                if (task) task.processing_status = 'processing';
                applyFilters();

                showProcessingModal(taskId);
                fetch('/api/tasks/' + taskId + '/process', { method: 'POST' }).then(() => loadTasks());
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

            let _precheckTaskId = null;

            async function sendComment() {
                if (!_precheckTaskId) return;
                const body = document.getElementById('precheckComment').value;
                if (!body.trim()) return;
                if (!await customConfirm('Post this comment on GitHub?')) return;
                const btn = document.getElementById('sendCommentBtn');
                const original = btn.innerHTML;
                btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i> Sending...';
                btn.disabled = true;
                try {
                    const res = await fetch('/api/tasks/' + _precheckTaskId + '/comment', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({body: body}),
                    });
                    const data = await res.json();
                    if (data.success) {
                        btn.innerHTML = '<i class="fas fa-check mr-1"></i> Sent!';
                        setTimeout(() => { hidePrecheckModal(); }, 1500);
                    } else {
                        customAlert('Failed: ' + (data.error || 'Unknown error'));
                        btn.innerHTML = original;
                    }
                } catch (e) {
                    customAlert('Network error: ' + e.message);
                    btn.innerHTML = original;
                }
                btn.disabled = false;
            }

            function showProcessingModal(taskId) {
                const modal = document.getElementById('processingModal');
                modal.classList.remove('hidden');
                modal.classList.add('flex');
                
                if (window._pollInterval) {
                    clearInterval(window._pollInterval);
                }
                if (window._elapsedInterval) {
                    clearInterval(window._elapsedInterval);
                }

                document.getElementById('processingStats').classList.add('hidden');
                window._processingStartTime = Date.now();
                window._elapsedInterval = setInterval(updateElapsed, 1000);
                updateElapsed();
                
                pollTaskStatus(taskId);
            }

            function updateElapsed() {
                const el = document.getElementById('processingElapsed');
                if (!el || !window._processingStartTime) return;
                const diff = Math.floor((Date.now() - window._processingStartTime) / 1000);
                const m = Math.floor(diff / 60);
                const s = diff % 60;
                el.textContent = String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
            }

            function hideProcessingModal() {
                const modal = document.getElementById('processingModal');
                modal.classList.add('hidden');
                modal.classList.remove('flex');
                if (window._pollInterval) {
                    clearInterval(window._pollInterval);
                    window._pollInterval = null;
                }
                if (window._elapsedInterval) {
                    clearInterval(window._elapsedInterval);
                    window._elapsedInterval = null;
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
                            if (window._elapsedInterval) {
                                clearInterval(window._elapsedInterval);
                                window._elapsedInterval = null;
                            }
                            loadTasks();

                            if (status.model_used || status.token_stats) {
                                const stats = {
                                    total_duration: status.duration || parseFloat(document.getElementById('processingElapsed').textContent.split(':').reduce((m,s) => m*60+parseFloat(s), 0)) || 0,
                                    total_tokens: (status.token_stats?.total_tokens) || (status.token_stats?.prompt_tokens || 0) + (status.token_stats?.completion_tokens || 0),
                                    total_prompt_tokens: status.token_stats?.prompt_tokens || 0,
                                    total_completion_tokens: status.token_stats?.completion_tokens || 0,
                                    models: {},
                                    current_step: status.step || '',
                                };
                                if (status.model_used) {
                                    stats.models[status.model_used] = {
                                        tokens_per_sec: stats.total_duration > 0 ? stats.total_tokens / stats.total_duration : 0,
                                        total_tokens: stats.total_tokens,
                                        prompt_tokens: stats.total_prompt_tokens,
                                        completion_tokens: stats.total_completion_tokens,
                                        duration: stats.total_duration,
                                    };
                                }
                                renderStatsBanner('processingStats', stats, { animate: true, pulse: true });
                            }

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
                }, 3000);
            }

            async function approveReview(id) {
                if (!await customConfirm('Approve and create PR?')) return;
                const res = await fetch('/api/reviews/' + id + '/approve', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({}) });
                const data = await res.json();
                customAlert(data.pr_url ? 'PR Created: ' + data.pr_url : 'Approved!');
                loadReviews(); refreshStatus();
            }
            async function rejectReview(id) { const c = await customPrompt('Reason for rejection:'); if (c === null || !c.trim()) return; await fetch('/api/reviews/' + id + '/reject', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({comments: c}) }); loadReviews(); }
            async function trashReview(reviewId, taskId) {
                if (!await customConfirm('Delete this task permanently?')) return;
                try {
                    await fetch('/api/tasks/' + taskId + '/delete', { method: 'DELETE' });
                    await fetch('/api/reviews/' + reviewId + '/delete', { method: 'DELETE' });
                    loadReviews();
                    refreshAll();
                } catch (e) { customAlert('Delete failed: ' + e.message); }
            }
            async function startSystem() { openStartModal(); }
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
                if (!await customConfirm(`Delete ${ids.length} task(s)?`)) return;
                const res = await fetch('/api/tasks/delete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ids})
                });
                const data = await res.json();
                customAlert(`Deleted ${data.deleted} task(s)`);
                loadTasks();
            }

            async function retryTask(id) {
                if (!await customConfirm('Retry this task? It will be reset and processed again immediately.')) return;
                try {
                    const res = await fetch('/api/tasks/' + id + '/retry', { method: 'POST' });
                    const data = await res.json();
                    if (data.success) {
                        loadTasks();
                        showProcessingModal(id);
                    } else {
                        customAlert('Retry failed: ' + (data.error || 'Unknown error'));
                    }
                } catch (e) {
                    customAlert('Retry failed: ' + e.message);
                }
            }

            async function deleteTaskWorkspace(id) {
                if (!await customConfirm('Delete local files for this task? This cannot be undone.')) return;
                try {
                    const res = await fetch('/api/tasks/' + id + '/workspace', { method: 'DELETE' });
                    const data = await res.json();
                    if (data.success) {
                        customAlert('Local files deleted');
                        loadTasks();
                    } else {
                        customAlert('Failed: ' + (data.error || 'Unknown error'));
                    }
                } catch (e) {
                    customAlert('Failed: ' + e.message);
                }
            }

            async function deleteFailedTask(id) {
                if (!await customConfirm('Delete this failed task and all its files? This cannot be undone.')) return;
                try {
                    const res = await fetch('/api/tasks/' + id + '/delete', { method: 'DELETE' });
                    const data = await res.json();
                    if (data.success) {
                        customAlert('Task deleted');
                        loadTasks();
                    } else {
                        customAlert('Failed: ' + (data.error || 'Unknown error'));
                    }
                } catch (e) {
                    customAlert('Failed: ' + e.message);
                }
            }

            async function clearAllUntouched() {
                if (!await customConfirm('Delete all untouched (new/pending) tasks and their workspace files? This cannot be undone.')) return;
                const res = await fetch('/api/tasks/clear-untouched', { method: 'POST' });
                const data = await res.json();
                customAlert(`Cleared ${data.deleted} task(s)`);
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
                    customAlert('No failed tasks to retry');
                    return;
                }
                if (!await customConfirm(`Retry ${failedIds.length} failed task(s)?`)) return;
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
                customAlert(`Retried ${successCount} of ${failedIds.length} task(s)`);
                loadTasks();
            }

            async function deleteAllFailed() {
                if (!window.allTasks) return;
                const isFailed = (t) => {
                    const st = (t.processing_status || 'new') === 'pending' ? 'new' : t.processing_status;
                    return ['failed', 'validation_failed', 'review_failed', 'error'].includes(st);
                };
                const failedIds = window.allTasks.filter(isFailed).map(t => t.id);
                if (failedIds.length === 0) {
                    customAlert('No failed tasks to delete');
                    return;
                }
                if (!await customConfirm(`Delete ${failedIds.length} failed task(s) and all their files? This cannot be undone.`)) return;
                let successCount = 0;
                for (const id of failedIds) {
                    try {
                        const res = await fetch('/api/tasks/' + id + '/delete', { method: 'DELETE' });
                        const data = await res.json();
                        if (data.success) successCount++;
                    } catch (e) {
                        console.error('Delete failed for task', id, e);
                    }
                }
                customAlert(`Deleted ${successCount} of ${failedIds.length} task(s)`);
                loadTasks();
            }

            async function killTask(taskId) {
                if (!await customConfirm('Kill this task immediately? Model resources will be released.')) return;
                try {
                    await fetch('/api/tasks/' + taskId + '/kill', { method: 'POST' });
                    refreshAll();
                } catch (e) { customAlert('Kill failed: ' + e.message); }
            }

            async function resetTask(taskId) {
                if (!await customConfirm('Reset this task back to "new" status?')) return;
                try {
                    const res = await fetch('/api/tasks/' + taskId + '/reset', { method: 'POST' });
                    const data = await res.json();
                    customAlert(data.message || 'Task reset');
                    loadTasks();
                } catch (e) {
                    customAlert('Reset failed: ' + e.message);
                }
            }

            async function resetAllProcessing() {
                const processing = window.allTasks.filter(t => t.processing_status === 'processing');
                if (processing.length === 0) {
                    customAlert('No processing tasks to reset');
                    return;
                }
                if (!await customConfirm(`Reset ${processing.length} stuck task(s) back to "new"?`)) return;
                let count = 0;
                for (const t of processing) {
                    try {
                        const res = await fetch('/api/tasks/' + t.id + '/reset', { method: 'POST' });
                        const data = await res.json();
                        if (data.success) count++;
                    } catch (e) {
                        console.error('Reset failed for task', t.id, e);
                    }
                }
                customAlert(`Reset ${count} of ${processing.length} task(s)`);
                loadTasks();
            }

            setInterval(refreshAll, 3000);
            refreshAll();
            setInterval(() => {
                document.querySelectorAll('[id^="elapsed_"]').forEach(el => {
                    const id = el.id.replace('elapsed_', '');
                    const t = window.allTasks.find(x => x.id == id);
                    if (t && (t.processing_status === 'processing' || t.processing_status === 'pending')) {
                        el.textContent = _computeElapsed(t);
                    }
                });
            }, 1000);
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
    from ..core.config import config as app_config

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
    sandbox_enabled = app_config.get('sandbox', {}).get('enabled', True)
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

    # System resources
    try:
        import psutil
        stats['system'] = {
            'cpu_percent': psutil.cpu_percent(interval=0.1),
            'ram_used': psutil.virtual_memory().used,
            'ram_total': psutil.virtual_memory().total,
            'ram_percent': psutil.virtual_memory().percent,
            'disk_used': psutil.disk_usage('/').used,
            'disk_total': psutil.disk_usage('/').total,
            'disk_percent': psutil.disk_usage('/').percent,
        }
    except ImportError:
        stats['system'] = {'available': False}

    # Ollama loaded models
    try:
        resp = subprocess.run(
            ['curl', '-s', 'http://localhost:11434/api/ps'],
            capture_output=True, text=True, timeout=5
        )
        if resp.returncode == 0:
            import json as _json
            data = _json.loads(resp.stdout)
            models = []
            for m in data.get('models', []):
                name = m.get('name', '')
                total_size = m.get('size', 0)
                vram_size = m.get('size_vram', 0)
                ctx = m.get('context_length', 0)
                details = m.get('details', {})
                param_size = details.get('parameter_size', '')

                # Calculate GPU % based on how much of the model is in VRAM
                gpu_pct = round((vram_size / total_size * 100)) if total_size > 0 else 0
                processor = 'GPU' if gpu_pct == 100 else f'{gpu_pct}% GPU'

                models.append({
                    'name': name,
                    'param_size': param_size,
                    'size_gb': total_size / 1073741824,
                    'processor': processor,
                    'context': ctx,
                })
            stats['ollama_loaded'] = models
        else:
            stats['ollama_loaded'] = []
    except Exception:
        stats['ollama_loaded'] = []

    # Running containers
    if runtime:
        try:
            r = subprocess.run([runtime, 'ps', '--format', '{{.Names}}'],
                               capture_output=True, text=True, timeout=5)
            containers = [c.strip() for c in r.stdout.strip().split('\n') if c.strip()]
            stats['containers'] = containers
        except Exception:
            stats['containers'] = []
    else:
        stats['containers'] = []

    # Active agents from task processor
    active = task_processor.get_active_tasks()
    agents_info = []
    for tid, tstatus in active.items():
        step = tstatus.get('step', '')
        agents_info.append({
            'task_id': tid,
            'step': step,
        })
    stats['active_agents'] = agents_info

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

    if status_filter == 'rejected':
        reviews = db.get_rejected_reviews()
    else:
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
        db.update_bounty_status(bounty_id, 'rejected')
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


@app.route('/api/reviews/<int:review_id>/retry', methods=['POST'])
def retry_review(review_id):
    reviews = db.get_rejected_reviews()
    review = next((r for r in reviews if r['id'] == review_id), None)
    if not review:
        return jsonify({'error': 'Review not found or not in rejected state'}), 404

    bounty_id = review['bounty_id']
    db.update_bounty_status(bounty_id, 'new')
    db.update_review(review_id, 'retried', 'Retrying')
    from ..core.sandbox import cleanup_workspace
    cleanup_workspace(bounty_id)

    task_id_str = str(bounty_id)
    if task_id_str in task_processor._status:
        del task_processor._status[task_id_str]
    if task_id_str in task_processor._logs:
        del task_processor._logs[task_id_str]

    result = orchestrator.process_single_bounty(bounty_id)
    return jsonify({'success': True, 'message': 'Task restarted', 'auto_started': True})


@app.route('/api/reviews/<int:review_id>/delete', methods=['DELETE'])
def delete_review(review_id):
    with db.get_connection() as conn:
        conn.cursor().execute("DELETE FROM review_queue WHERE id = ?", (review_id,))
    return jsonify({'success': True})


@app.route('/api/start/config', methods=['GET'])
def get_start_config():
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500
    return jsonify(orchestrator.get_start_config())


@app.route('/api/start', methods=['POST'])
def start_orchestrator():
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500

    data = request.get_json() or {}
    orchestrator.start(**data)

    return jsonify({'success': True, 'message': 'Orchestrator started'})


@app.route('/api/stop', methods=['POST'])
def stop_orchestrator():
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500

    orchestrator.stop()

    return jsonify({'success': True, 'message': 'Orchestrator stopped'})


@app.route('/api/scan', methods=['POST'])
def scan_tasks():
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500

    data = request.get_json() or {}
    test_mode = data.get('test_mode', True)
    min_price = data.get('min_price', 0)
    max_price = data.get('max_price', 0)
    limit = data.get('limit', 10)
    labels = data.get('labels')

    count = orchestrator.manual_scan(
        test_mode=test_mode,
        labels=labels,
        limit=limit,
        min_price=min_price,
        max_price=max_price
    )

    return jsonify({'success': True, 'tasks_found': count})


@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    from ..core.task_processor import task_processor
    bounties = db.get_all_bounties()
    for b in bounties:
        for key in ('fetched_at', 'created_at', 'updated_at'):
            if b.get(key) and 'Z' not in str(b[key]) and '+' not in str(b[key]):
                b[key] = str(b[key]) + '+00:00'
        if b.get('processing_status') in ('processing', 'queued'):
            tp_status = task_processor.get_status(str(b['id']))
            if tp_status and tp_status.get('started_at'):
                b['started_at'] = tp_status['started_at']
    return jsonify(bounties)


@app.route('/api/tasks/running', methods=['GET'])
def get_running_tasks():
    count = db.get_running_tasks_count()
    return jsonify({'count': count})


@app.route('/api/models', methods=['GET'])
def get_available_models():
    import requests as req
    from ..core.config import config as app_config
    from ..utils.logger import get_logger
    logger = get_logger(__name__)

    models = {'ollama': [], 'opencode': []}

    ollama_url = app_config.ollama.get('base_url', 'http://localhost:11434')
    try:
        resp = req.get(f'{ollama_url}/api/tags', timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            for m in data.get('models', []):
                models['ollama'].append({
                    'name': m['name'],
                    'source': 'ollama',
                })
    except Exception as e:
        logger.warning(f'Failed to fetch Ollama models: {e}')

    opencode_cfg = app_config.opencode
    opencode_key = opencode_cfg.get('api_key', '')
    opencode_url = opencode_cfg.get('base_url', 'https://api.opencode.ai')

    if opencode_key and not opencode_key.startswith('YOUR'):
        try:
            resp = req.get(f'{opencode_url}/v1/models', timeout=10, headers={
                'Authorization': f'Bearer {opencode_key}',
            })
            if resp.status_code == 200:
                data = resp.json()
                for m in data.get('data', []):
                    models['opencode'].append({
                        'name': m['id'],
                        'source': 'opencode',
                    })
                logger.info(f'Loaded {len(data["data"])} OpenCode models')
            else:
                logger.warning(f'OpenCode models request failed: {resp.status_code} {resp.text[:200]}')
        except Exception as e:
            logger.warning(f'Failed to fetch OpenCode models: {e}')
    else:
        logger.info('OpenCode not configured, skipping model fetch')

    return jsonify(models)


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
            'ollama_base_url': cfg.get('ollama', {}).get('base_url', ''),
            'sandbox': cfg.get('sandbox', {}),
            'workspace': cfg.get('workspace', {}),
            'agents': cfg.get('agents', {}),
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

        if 'agents' in data:
            a = data['agents']
            if 'roles' in a:
                if 'roles' not in cfg['agents']:
                    cfg['agents']['roles'] = {}
                for k, v in a['roles'].items():
                    if v:
                        cfg['agents']['roles'][k] = v
            if 'max_local_fix_cycles' in a:
                cfg['agents']['max_local_fix_cycles'] = int(a['max_local_fix_cycles'])
            if 'max_send_back' in a:
                cfg['agents']['max_send_back'] = int(a['max_send_back'])
            if 'max_concurrent_tasks' in a:
                cfg['agents']['max_concurrent_tasks'] = int(a['max_concurrent_tasks'])

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


@app.route('/api/logs', methods=['DELETE'])
def clear_logs():
    bounty_id = request.args.get('bounty_id', type=int)
    try:
        db.clear_processing_logs(bounty_id=bounty_id)
        if bounty_id:
            task_id_str = str(bounty_id)
            if task_id_str in task_processor._logs:
                del task_processor._logs[task_id_str]
        return jsonify({'success': True, 'message': 'Logs cleared'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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

    from ..core.sandbox import cleanup_workspace
    cleanup_workspace(task_id)

    db.update_bounty_status(task_id, 'new')
    db.log_processing(task_id, 'system', 'retried', 'new', 'Task reset to new status for retry')
    
    task_id_str = str(task_id)
    if task_id_str in task_processor._status:
        del task_processor._status[task_id_str]
    if task_id_str in task_processor._logs:
        del task_processor._logs[task_id_str]
    
    result = orchestrator.process_single_bounty(task_id)
    return jsonify({'success': True, 'message': 'Task restarted', 'auto_started': True})


@app.route('/api/tasks/<int:task_id>/kill', methods=['POST'])
def kill_task_api(task_id):
    from ..core.sandbox import kill_containers_for_bounty, cleanup_workspace
    bounty = db.get_bounty_by_id(task_id)
    if not bounty:
        return jsonify({'error': 'Task not found'}), 404

    was_active = task_processor.cancel(str(task_id))
    killed = kill_containers_for_bounty(task_id)
    cleanup_workspace(task_id)
    db.update_bounty_status(task_id, 'failed')
    db.log_processing(task_id, 'system', 'killed', 'failed', f'Task killed by user')

    task_id_str = str(task_id)
    if task_id_str in task_processor._status:
        task_processor._status[task_id_str]['status'] = 'failed'

    msg = 'Task killed'
    if was_active:
        msg += ' (was actively processing)'
    if killed:
        msg += f' (killed {killed} container(s))'

    return jsonify({'success': True, 'message': msg})


@app.route('/api/tasks/<int:task_id>/reset', methods=['POST'])
def reset_task_api(task_id):
    bounty = db.get_bounty_by_id(task_id)
    if not bounty:
        return jsonify({'error': 'Task not found'}), 404

    # Cancel in task processor (sets flag to abort if currently running)
    was_active = task_processor.cancel(str(task_id))

    # Kill any running containers for this bounty
    from ..core.sandbox import kill_containers_for_bounty
    killed = kill_containers_for_bounty(task_id)

    # Discard stale workspace so next run gets a fresh clone
    from ..core.sandbox import cleanup_workspace
    cleanup_workspace(task_id)

    # Reset DB status
    db.update_bounty_status(task_id, 'new')
    db.log_processing(task_id, 'system', 'reset', 'new', f'Task manually reset to new (was_active={was_active}, killed={killed})')

    # Clear in-memory status and logs
    task_id_str = str(task_id)
    if task_id_str in task_processor._status:
        del task_processor._status[task_id_str]
    if task_id_str in task_processor._logs:
        del task_processor._logs[task_id_str]

    msg = 'Task reset to new'
    if was_active:
        msg += ' (was actively processing)'
    if killed:
        msg += f' (killed {killed} container(s))'

    return jsonify({'success': True, 'message': msg})


@app.route('/api/tasks/<int:task_id>/precheck', methods=['GET'])
def precheck_task(task_id):
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500
    result = orchestrator.pre_check_bounty(task_id)
    return jsonify(result)


@app.route('/api/tasks/<int:task_id>/plan-attempt', methods=['POST'])
def plan_attempt_task(task_id):
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500
    result = orchestrator.plan_attempt(task_id)
    return jsonify(result)


@app.route('/api/tasks/<int:task_id>/plan-attempt-preview', methods=['GET'])
def plan_attempt_preview(task_id):
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500
    result = orchestrator.plan_attempt_preview(task_id)
    return jsonify(result)


@app.route('/api/tasks/<int:task_id>/submit-attempt', methods=['POST'])
def submit_attempt(task_id):
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500
    data = request.get_json() or {}
    body = data.get('body', '')
    execute = data.get('execute', False)
    result = orchestrator.submit_attempt_comment(task_id, body, execute)
    return jsonify(result)


@app.route('/api/tasks/<int:task_id>/execute', methods=['POST'])
def execute_task(task_id):
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500
    result = orchestrator.execute_bounty(task_id)
    return jsonify(result)


@app.route('/api/tasks/<int:task_id>/process', methods=['POST'])
def process_task(task_id):
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500
    result = orchestrator.process_single_bounty(task_id)
    return jsonify(result)


@app.route('/api/tasks/<int:task_id>/comment', methods=['POST'])
def post_task_comment(task_id):
    if not orchestrator:
        return jsonify({'error': 'Orchestrator not initialized'}), 500
    data = request.get_json() or {}
    body = data.get('body', '')
    if not body:
        return jsonify({'success': False, 'error': 'Comment body is empty'}), 400
    result = orchestrator.post_comment(task_id, body)
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


@app.route('/api/tasks/<int:task_id>/delete', methods=['DELETE'])
def delete_task(task_id):
    from ..core.sandbox import cleanup_workspace
    from pathlib import Path
    from ..core.config import config

    bounty = db.get_bounty_by_id(task_id)
    if not bounty:
        return jsonify({'success': False, 'error': 'Task not found'}), 404

    # Delete workspace files
    cleanup_workspace(task_id)

    # Delete from database
    with db.get_connection() as conn:
        conn.cursor().execute("DELETE FROM bounties WHERE id = ?", (task_id,))
        conn.cursor().execute("DELETE FROM processing_logs WHERE bounty_id = ?", (task_id,))
        conn.cursor().execute("DELETE FROM review_queue WHERE bounty_id = ?", (task_id,))
        conn.commit()

    # Clear in-memory state
    task_id_str = str(task_id)
    if task_id_str in task_processor._status:
        del task_processor._status[task_id_str]
    if task_id_str in task_processor._logs:
        del task_processor._logs[task_id_str]

    return jsonify({'success': True, 'message': 'Task deleted'})


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
    from ..core.sandbox import cleanup_workspace
    from pathlib import Path
    from ..core.config import config

    # Get all untouched tasks first
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM bounties WHERE processing_status IN ('new', 'pending')")
        untouched_ids = [row[0] for row in cursor.fetchall()]

    deleted_count = 0
    for task_id in untouched_ids:
        # Delete workspace files
        cleanup_workspace(task_id)

        # Delete from database
        with db.get_connection() as conn:
            conn.cursor().execute("DELETE FROM bounties WHERE id = ?", (task_id,))
            conn.cursor().execute("DELETE FROM processing_logs WHERE bounty_id = ?", (task_id,))
            conn.commit()

        # Clear in-memory state
        task_id_str = str(task_id)
        if task_id_str in task_processor._status:
            del task_processor._status[task_id_str]
        if task_id_str in task_processor._logs:
            del task_processor._logs[task_id_str]

        deleted_count += 1

    return jsonify({'success': True, 'deleted': deleted_count})


def run_server(port: int = 5000, debug: bool = False):
    global orchestrator, _startup_time
    import sys as _sys
    import os as _os
    import signal as _signal
    from ..core.task_processor import task_processor
    _startup_time = time.time()
    orchestrator = BountyFactoryOrchestrator()
    task_processor.start()

    _shutting_down = False

    def _shutdown(sig, frame):
        nonlocal _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        print(f"\nReceived {_signal.Signals(sig).name}, shutting down...", flush=True)
        orchestrator.stop()
        _os._exit(0)

    _signal.signal(_signal.SIGINT, _shutdown)
    _signal.signal(_signal.SIGTERM, _shutdown)

    app.run(host='0.0.0.0', port=port, debug=debug)


if __name__ == '__main__':
    run_server()