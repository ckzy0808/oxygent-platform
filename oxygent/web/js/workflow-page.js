(function () {
    'use strict';

    var root = document.getElementById('workflow-page');
    if (!root) return;
    var api = window.OxyGentApp.api;
    var phaseLabels = {
        requirement: 'Requirement', architecture: 'Architecture', plan: 'Plan',
        implementation: 'Implementation', verification: 'Verification',
        review: 'Review', approval: 'Approval'
    };
    var statusLabels = {
        'not-started': 'Not started', analyzing: 'Analyzing', planning: 'Planning',
        implementing: 'Implementing', testing: 'Testing', reviewing: 'Reviewing',
        'awaiting-approval': 'Awaiting approval', completed: 'Completed',
        blocked: 'Blocked', failed: 'Failed'
    };
    var state = {runs: [], selectedRunId: '', selectedPhase: '', events: [], drawerOpen: false};

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function formatCost(value) { return '$' + Number(value || 0).toFixed(4); }
    function formatDuration(value) {
        var milliseconds = Number(value || 0);
        return milliseconds >= 60000 ? (milliseconds / 60000).toFixed(1) + ' min' : Math.round(milliseconds) + ' ms';
    }
    function formatTime(value) {
        if (!value) return '—';
        return new Date(value).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
    }
    function statusPill(status) {
        return '<span class="og-workflow-status og-status-' + escapeHtml(status) + '">' + escapeHtml(statusLabels[status] || status) + '</span>';
    }

    function header(run) {
        return '<header class="og-workspace-header"><div><p class="og-workspace-eyebrow">Structured collaboration</p>' +
            '<h1 class="og-workspace-title">Workflow Timeline</h1></div>' +
            (run ? statusPill(run.status) : '<span class="og-preview-badge">PR 4</span>') + '</header>';
    }

    function emptyState(title, message) {
        return '<section class="og-project-empty"><div class="og-empty-icon">◇</div><h2>' + escapeHtml(title) + '</h2><p>' + escapeHtml(message) + '</p></section>';
    }

    function phaseRail(run) {
        return '<ol class="og-phase-rail" aria-label="Workflow phases">' + run.stages.map(function (stage, index) {
            var current = stage.phase === run.currentPhase ? ' current' : '';
            return '<li class="og-phase-node og-phase-' + escapeHtml(stage.status) + current + '"><button data-phase="' + escapeHtml(stage.phase) + '">' +
                '<span class="og-phase-index">' + (index + 1) + '</span><span><b>' + escapeHtml(phaseLabels[stage.phase]) + '</b><small>' + escapeHtml(statusLabels[stage.status] || stage.status) + '</small></span></button></li>';
        }).join('') + '</ol>';
    }

    function runToolbar(run) {
        return '<section class="og-run-toolbar"><div class="og-run-heading"><label for="workflow-run-select">Workflow run</label>' +
            '<select id="workflow-run-select">' + state.runs.map(function (item) {
                return '<option value="' + escapeHtml(item.runId) + '"' + (item.runId === run.runId ? ' selected' : '') + '>' + escapeHtml(item.name) + '</option>';
            }).join('') + '</select><p>Project <code>' + escapeHtml(run.projectId) + '</code> · Task <code>' + escapeHtml(run.taskId) + '</code></p></div>' +
            '<div class="og-run-metrics"><span><small>Total cost</small><b>' + formatCost(run.totalCost) + '</b></span><span><small>Duration</small><b>' + formatDuration(run.totalDurationMs) + '</b></span>' +
            '<button class="og-secondary-button" id="open-execution-drawer">Execution details</button></div></section>';
    }

    function stageCard(stage) {
        var selected = stage.phase === state.selectedPhase ? ' selected' : '';
        return '<article class="og-stage-card' + selected + '" data-stage-card="' + escapeHtml(stage.phase) + '"><div class="og-stage-marker"></div><div class="og-stage-body">' +
            '<div class="og-stage-head"><div><span>' + escapeHtml(phaseLabels[stage.phase]) + '</span><h2>' + escapeHtml(stage.summary || 'No phase output yet') + '</h2></div>' + statusPill(stage.status) + '</div>' +
            '<div class="og-stage-meta"><span><small>Role</small><b>' + escapeHtml(stage.roleName || 'Unassigned') + '</b></span><span><small>Agent</small><b>' + escapeHtml(stage.agentId || '—') + '</b></span>' +
            '<span><small>Provider</small><b>' + escapeHtml(stage.providerName || '—') + '</b></span><span><small>Model</small><b>' + escapeHtml(stage.modelName || '—') + '</b></span></div>' +
            '<div class="og-stage-footer"><span>' + stage.eventCount + ' events</span><span>' + formatCost(stage.cost) + '</span><span>' + formatDuration(stage.durationMs) + '</span></div></div></article>';
    }

    function stageDetails(stage) {
        var tools = stage.toolsUsed && stage.toolsUsed.length ? stage.toolsUsed.map(function (tool) { return '<span>' + escapeHtml(tool) + '</span>'; }).join('') : '<em>None recorded</em>';
        var artifact = stage.artifact ? '<a href="projects.html?artifact=' + encodeURIComponent(stage.artifact.id || '') + '"><b>' + escapeHtml(stage.artifact.type || 'Artifact') + '</b><small>' + escapeHtml(stage.artifact.id || '') + '</small></a>' : '<p>No Artifact attached to this phase.</p>';
        return '<aside class="og-stage-detail"><div class="og-detail-eyebrow">Selected phase</div><h2>' + escapeHtml(phaseLabels[stage.phase]) + '</h2>' + statusPill(stage.status) +
            '<p class="og-detail-summary">' + escapeHtml(stage.summary || 'This phase has not started.') + '</p><dl><dt>Role</dt><dd>' + escapeHtml(stage.roleName || 'Unassigned') + '</dd><dt>Provider</dt><dd>' + escapeHtml(stage.providerName || '—') +
            '</dd><dt>Model</dt><dd>' + escapeHtml(stage.modelName || '—') + '</dd><dt>Updated</dt><dd>' + escapeHtml(formatTime(stage.updatedAt)) + '</dd></dl>' +
            '<section><h3>Tools used</h3><div class="og-tool-chips">' + tools + '</div></section><section><h3>Artifact</h3><div class="og-stage-artifact">' + artifact + '</div></section>' +
            '<p class="og-private-note">Routing facts and execution metadata are shown. Private model reasoning is never displayed.</p></aside>';
    }

    function eventRow(event) {
        var payload = event.payload || {};
        var metadata = [];
        if (payload.toolName) metadata.push('Tool: ' + payload.toolName);
        if (payload.exitCode != null) metadata.push('Exit: ' + payload.exitCode);
        if (payload.durationMs != null) metadata.push(formatDuration(payload.durationMs));
        if (payload.cost != null) metadata.push(formatCost(payload.cost));
        return '<article class="og-event-row"><div class="og-event-dot"></div><div><div class="og-event-head"><b>' + escapeHtml(event.eventType) + '</b><time>' + escapeHtml(formatTime(event.timestamp)) + '</time></div>' +
            '<p>' + escapeHtml(payload.summary || payload.message || 'Execution metadata recorded.') + '</p><small>' + escapeHtml([event.providerName, event.modelName].filter(Boolean).join(' · ') || 'System event') + '</small>' +
            (metadata.length ? '<div class="og-event-metadata">' + metadata.map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join('') + '</div>' : '') + '</div></article>';
    }

    function drawer(run) {
        return '<div class="og-drawer-backdrop' + (state.drawerOpen ? ' open' : '') + '" id="execution-backdrop"></div><aside class="og-execution-drawer' + (state.drawerOpen ? ' open' : '') + '" aria-label="Advanced execution details">' +
            '<header><div><span>Advanced</span><h2>Execution details</h2><p>' + escapeHtml(run.runId) + '</p></div><button id="close-execution-drawer" aria-label="Close">×</button></header>' +
            '<div class="og-drawer-notice">Product-safe event metadata only. Prompts, raw model output, credentials, and private reasoning are excluded.</div><div class="og-event-list">' +
            (state.events.length ? state.events.map(eventRow).join('') : '<div class="og-drawer-loading">Open the drawer to load events.</div>') + '</div></aside>';
    }

    function render() {
        var run = state.runs.find(function (item) { return item.runId === state.selectedRunId; });
        if (!run) {
            root.innerHTML = header() + '<div class="og-workspace-content">' + emptyState('No Workflow runs', 'Workflow events will appear here when a structured run starts.') + '</div>';
            return;
        }
        if (!state.selectedPhase) state.selectedPhase = run.currentPhase || run.stages[0].phase;
        var stage = run.stages.find(function (item) { return item.phase === state.selectedPhase; }) || run.stages[0];
        root.innerHTML = header(run) + '<div class="og-workflow-content">' + runToolbar(run) + phaseRail(run) +
            '<div class="og-workflow-layout"><section class="og-stage-timeline">' + run.stages.map(stageCard).join('') + '</section>' + stageDetails(stage) + '</div></div>' + drawer(run);
        bindEvents();
    }

    async function openDrawer() {
        state.drawerOpen = true;
        render();
        if (!state.events.length) {
            try {
                var result = await api.listWorkflowEvents(state.selectedRunId);
                state.events = result.items || [];
            } catch (error) {
                state.events = [{eventType: 'events.unavailable', timestamp: new Date().toISOString(), payload: {message: error.message}}];
            }
            render();
        }
    }

    function bindEvents() {
        var selector = document.getElementById('workflow-run-select');
        if (selector) selector.addEventListener('change', function () {
            state.selectedRunId = selector.value;
            state.selectedPhase = '';
            state.events = [];
            render();
        });
        root.querySelectorAll('[data-phase], [data-stage-card]').forEach(function (node) {
            node.addEventListener('click', function () {
                state.selectedPhase = node.getAttribute('data-phase') || node.getAttribute('data-stage-card');
                render();
            });
        });
        var open = document.getElementById('open-execution-drawer');
        if (open) open.addEventListener('click', openDrawer);
        var close = document.getElementById('close-execution-drawer');
        if (close) close.addEventListener('click', function () { state.drawerOpen = false; render(); });
        var backdrop = document.getElementById('execution-backdrop');
        if (backdrop) backdrop.addEventListener('click', function () { state.drawerOpen = false; render(); });
    }

    async function mount() {
        root.innerHTML = header() + '<div class="og-workspace-content"><div class="og-loading-state">Loading Workflow Timeline…</div></div>';
        var params = new URLSearchParams(window.location.search);
        try {
            var result = await api.listWorkflowRuns({projectId: params.get('projectId'), taskId: params.get('taskId')});
            state.runs = result.items || [];
            state.selectedRunId = params.get('runId') || (state.runs[0] && state.runs[0].runId) || '';
            render();
        } catch (error) {
            root.innerHTML = header() + '<div class="og-workspace-content">' + emptyState('Workflow Timeline unavailable', error.message) + '</div>';
        }
    }

    mount();
})();
