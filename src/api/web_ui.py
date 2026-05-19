<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bounty Factory</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; }
        .status-dot.green { background-color: #22c55e; }
        .status-dot.red { background-color: #ef4444; }
        .status-dot.yellow { background-color: #eab308; }
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
                    <div class="flex items-center space-x-4">
                        <button onclick="startSystem()" class="bg-green-600 hover:bg-green-700 px-4 py-2 rounded">Start</button>
                        <button onclick="stopSystem()" class="bg-red-600 hover:bg-red-700 px-4 py-2 rounded">Stop</button>
                        <button onclick="refreshStatus()" class="bg-gray-600 hover:bg-gray-700 px-4 py-2 rounded">
                            <i class="fas fa-sync"></i>
                        </button>
                    </div>
                </div>
            </div>
        </nav>

        <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
            <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
                <div class="bg-gray-800 rounded-lg p-6">
                    <div class="text-gray-400 text-sm">System Status</div>
                    <div class="flex items-center mt-2">
                        <span id="systemStatus" class="status-dot yellow"></span>
                        <span id="systemStatusText" class="ml-2">Loading...</span>
                    </div>
                </div>
                <div class="bg-gray-800 rounded-lg p-6">
                    <div class="text-gray-400 text-sm">Pending Reviews</div>
                    <div class="text-3xl font-bold mt-2" id="pendingCount">-</div>
                </div>
                <div class="bg-gray-800 rounded-lg p-6">
                    <div class="text-gray-400 text-sm">Agents</div>
                    <div class="mt-2 space-y-1">
                        <div id="classifierStatus">Classifier: ...</div>
                        <div id="simpleAgentStatus">Simple Agent: ...</div>
                        <div id="complexAgentStatus">Complex Agent: ...</div>
                    </div>
                </div>
                <div class="bg-gray-800 rounded-lg p-6">
                    <div class="text-gray-400 text-sm">Configuration</div>
                    <div class="mt-2 space-y-1 text-sm">
                        <div>OpenCode: <span id="opencodeStatus">...</span></div>
                        <div>GitHub: <span id="githubStatus">...</span></div>
                    </div>
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
                <div>
                    <h2 class="text-xl font-bold mb-4">
                        <i class="fas fa-tasks text-purple-500 mr-2"></i>Pending Reviews
                    </h2>
                    <div id="reviewsList" class="space-y-4">
                        <div class="text-gray-400">Loading reviews...</div>
                    </div>
                </div>

                <div>
                    <h2 class="text-xl font-bold mb-4">
                        <i class="fas fa-bug text-green-500 mr-2"></i>Recent Bounties
                    </h2>
                    <div id="bountiesList" class="space-y-4">
                        <div class="text-gray-400">Loading bounties...</div>
                    </div>
                </div>
            </div>
        </main>
    </div>

    <script>
        async function refreshStatus() {
            try {
                const statusRes = await fetch('/api/status');
                const status = await statusRes.json();

                document.getElementById('systemStatus').className = 'status-dot ' + (status.running ? 'green' : 'red');
                document.getElementById('systemStatusText').textContent = status.running ? 'Running' : 'Stopped';
                document.getElementById('pendingCount').textContent = status.pending_reviews || 0;

                const router = status.router_status || {};
                document.getElementById('classifierStatus').textContent = 'Classifier: ' + (router.classifier_available ? '✓' : '✗');
                document.getElementById('simpleAgentStatus').textContent = 'Simple Agent: ' + (router.simple_agent_available ? '✓' : '✗');
                document.getElementById('complexAgentStatus').textContent = 'Complex Agent: ' + (router.complex_agent_available ? '✓' : '✗');

                const configRes = await fetch('/api/config');
                const config = await configRes.json();
                document.getElementById('opencodeStatus').textContent = config.opencode?.api_key_set ? '✓' : '✗';
                document.getElementById('githubStatus').textContent = config.git?.configured ? '✓' : '✗';

            } catch (e) {
                console.error('Status refresh failed:', e);
            }
        }

        async function loadReviews() {
            try {
                const res = await fetch('/api/reviews?status=pending');
                const reviews = await res.json();

                const container = document.getElementById('reviewsList');
                if (reviews.length === 0) {
                    container.innerHTML = '<div class="text-gray-400">No pending reviews</div>';
                    return;
                }

                container.innerHTML = reviews.map(r => `
                    <div class="bg-gray-800 rounded-lg p-4 border border-gray-700">
                        <div class="flex justify-between items-start">
                            <div>
                                <h3 class="font-bold text-lg">${r.title}</h3>
                                <p class="text-gray-400 text-sm">${r.repository_name}</p>
                                <p class="text-sm mt-2">Agent: ${r.agent_type} | Confidence: ${(r.confidence_score * 100).toFixed(0)}%</p>
                            </div>
                            <div class="text-green-400 font-bold">$${r.price}</div>
                        </div>
                        <div class="mt-4 flex space-x-2">
                            <button onclick="approveReview(${r.id})" class="bg-green-600 hover:bg-green-700 px-3 py-1 rounded text-sm">Approve & Create PR</button>
                            <button onclick="rejectReview(${r.id})" class="bg-red-600 hover:bg-red-700 px-3 py-1 rounded text-sm">Reject</button>
                            <button onclick="skipReview(${r.id})" class="bg-gray-600 hover:bg-gray-700 px-3 py-1 rounded text-sm">Skip</button>
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                console.error('Load reviews failed:', e);
            }
        }

        async function loadBounties() {
            try {
                const res = await fetch('/api/bounties');
                const bounties = await res.json();

                const container = document.getElementById('bountiesList');
                if (bounties.length === 0) {
                    container.innerHTML = '<div class="text-gray-400">No bounties found</div>';
                    return;
                }

                container.innerHTML = bounties.slice(0, 10).map(b => `
                    <div class="bg-gray-800 rounded-lg p-4 border border-gray-700">
                        <div class="flex justify-between items-start">
                            <div>
                                <h3 class="font-bold">${b.title}</h3>
                                <p class="text-gray-400 text-sm">${b.repository_name || 'Unknown'}</p>
                                <p class="text-sm mt-1">Status: ${b.processing_status}</p>
                            </div>
                            <div class="text-green-400 font-bold">$${b.price}</div>
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                console.error('Load bounties failed:', e);
            }
        }

        async function approveReview(id) {
            if (!confirm('Approve and create PR? This will push the branch and create a pull request.')) return;
            const res = await fetch('/api/reviews/' + id + '/approve', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({})
            });
            const data = await res.json();
            alert(data.pr_url ? 'PR Created: ' + data.pr_url : 'Approved!');
            loadReviews();
            refreshStatus();
        }

        async function rejectReview(id) {
            const comment = prompt('Rejection reason:');
            if (comment === null) return;
            await fetch('/api/reviews/' + id + '/reject', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({comments: comment})
            });
            loadReviews();
        }

        async function skipReview(id) {
            await fetch('/api/reviews/' + id + '/skip', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({comments: ''})
            });
            loadReviews();
        }

        async function startSystem() {
            await fetch('/api/start', {method: 'POST'});
            refreshStatus();
        }

        async function stopSystem() {
            await fetch('/api/stop', {method: 'POST'});
            refreshStatus();
        }

        setInterval(() => {
            refreshStatus();
            loadReviews();
            loadBounties();
        }, 10000);

        refreshStatus();
        loadReviews();
        loadBounties();
    </script>
</body>
</html>