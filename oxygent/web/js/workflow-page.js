(function () {
    'use strict';

    var root = document.getElementById('workflow-page');
    if (!root) return;
    var api = window.OxyGentApp.api;
    var phaseLabels = {
        requirement: '需求', architecture: '架构', plan: '计划',
        implementation: '实现', verification: '验证',
        review: '审查', approval: '审批'
    };
    var statusLabels = {
        'not-started': '未开始', analyzing: '分析中', planning: '规划中',
        implementing: '实现中', testing: '测试中', reviewing: '审查中',
        'awaiting-implementation': '等待实现',
        'awaiting-approval': '等待审批', completed: '已完成',
        blocked: '已阻塞', failed: '失败'
    };
    var displayLabels = {
        'Product Manager': '产品经理', 'Solution Architect': '解决方案架构师', 'Technical Lead': '技术负责人', Reviewer: '审查员', approver: '审批人',
        RequirementSpec: '需求规格', ArchitectureDecision: '架构决策', TaskGraph: '任务图', ReviewReport: '审查报告',
        'phase.started': '阶段开始', 'phase.completed': '阶段完成', 'workflow.queued': '工作流排队', 'workflow.completed': '工作流完成', 'workflow.awaitingImplementation': '等待实现', 'workflow.failed': '工作流失败', 'approval.requested': '请求审批', 'events.unavailable': '事件不可用'
    };
    var state = {runs: [], selectedRunId: '', selectedPhase: '', events: [], drawerOpen: false, stream: null, refreshTimer: null};

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function formatDuration(value) {
        var milliseconds = Number(value || 0);
        return milliseconds >= 60000 ? (milliseconds / 60000).toFixed(1) + ' 分钟' : Math.round(milliseconds) + ' 毫秒';
    }
    function formatTime(value) {
        if (!value) return '—';
        return new Date(value).toLocaleString('zh-CN', {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
    }
    function statusPill(status) {
        return '<span class="og-workflow-status og-status-' + escapeHtml(status) + '">' + escapeHtml(statusLabels[status] || status) + '</span>';
    }
    function display(value) { return displayLabels[value] || value; }
    function isTerminal(run) {
        if (!run) return true;
        if (['awaiting-implementation', 'blocked', 'failed'].indexOf(run.status) !== -1) return true;
        return run.status === 'completed' && ['review', 'approval'].indexOf(run.currentPhase) !== -1;
    }

    function header(run) {
        return '<header class="og-workspace-header"><div><p class="og-workspace-eyebrow">结构化协作</p>' +
            '<h1 class="og-workspace-title">工作流时间线</h1></div>' +
            (run ? statusPill(run.status) : '<span class="og-preview-badge">工作流</span>') + '</header>';
    }

    function emptyState(title, message) {
        return '<section class="og-project-empty"><div class="og-empty-icon">◇</div><h2>' + escapeHtml(title) + '</h2><p>' + escapeHtml(message) + '</p></section>';
    }

    function phaseRail(run) {
        return '<ol class="og-phase-rail" aria-label="工作流阶段">' + run.stages.map(function (stage, index) {
            var current = stage.phase === run.currentPhase ? ' current' : '';
            return '<li class="og-phase-node og-phase-' + escapeHtml(stage.status) + current + '"><button data-phase="' + escapeHtml(stage.phase) + '">' +
                '<span class="og-phase-index">' + (index + 1) + '</span><span><b>' + escapeHtml(phaseLabels[stage.phase]) + '</b><small>' + escapeHtml(statusLabels[stage.status] || stage.status) + '</small></span></button></li>';
        }).join('') + '</ol>';
    }

    function runToolbar(run) {
        return '<section class="og-run-toolbar"><div class="og-run-heading"><label for="workflow-run-select">工作流运行</label>' +
            '<select id="workflow-run-select">' + state.runs.map(function (item) {
                return '<option value="' + escapeHtml(item.runId) + '"' + (item.runId === run.runId ? ' selected' : '') + '>' + escapeHtml(item.name) + '</option>';
            }).join('') + '</select><p>项目 <code>' + escapeHtml(run.projectId) + '</code> · 任务 <code>' + escapeHtml(run.taskId) + '</code></p></div>' +
            '<div class="og-run-metrics"><span><small>Token 总量</small><b>' + Number(run.totalTokens || 0).toLocaleString('zh-CN') + '</b></span><span><small>持续时间</small><b>' + formatDuration(run.totalDurationMs) + '</b></span>' +
            '<button class="og-secondary-button" id="open-execution-drawer">执行详情</button></div></section>';
    }

    function stageCard(stage) {
        var selected = stage.phase === state.selectedPhase ? ' selected' : '';
        return '<article class="og-stage-card' + selected + '" data-stage-card="' + escapeHtml(stage.phase) + '"><div class="og-stage-marker"></div><div class="og-stage-body">' +
            '<div class="og-stage-head"><div><span>' + escapeHtml(phaseLabels[stage.phase]) + '</span><h2>' + escapeHtml(stage.summary || '本阶段暂无输出') + '</h2></div>' + statusPill(stage.status) + '</div>' +
            '<div class="og-stage-meta"><span><small>角色</small><b>' + escapeHtml(display(stage.roleName) || '未分配') + '</b></span><span><small>智能体</small><b>' + escapeHtml(stage.agentId || '—') + '</b></span>' +
            '<span><small>服务商</small><b>' + escapeHtml(stage.providerName || '—') + '</b></span><span><small>模型</small><b>' + escapeHtml(stage.modelName || '—') + '</b></span></div>' +
            '<div class="og-stage-footer"><span>' + stage.eventCount + ' 个事件</span><span>' + Number((stage.inputTokens || 0) + (stage.outputTokens || 0)).toLocaleString('zh-CN') + ' Token</span><span>' + formatDuration(stage.durationMs) + '</span></div></div></article>';
    }

    function stageDetails(stage) {
        var tools = stage.toolsUsed && stage.toolsUsed.length ? stage.toolsUsed.map(function (tool) { return '<span>' + escapeHtml(tool) + '</span>'; }).join('') : '<em>暂无记录</em>';
        var artifact = stage.artifact ? '<a href="projects.html?project=' + encodeURIComponent(stage.projectId || state.runs.find(function (item) { return item.runId === state.selectedRunId; }).projectId) + '&tab=artifacts&artifact=' + encodeURIComponent(stage.artifact.id || '') + '"><b>' + escapeHtml(display(stage.artifact.type) || '产物') + '</b><small>' + escapeHtml(stage.artifact.id || '') + '</small></a>' : '<p>本阶段未关联产物。</p>';
        return '<aside class="og-stage-detail"><div class="og-detail-eyebrow">所选阶段</div><h2>' + escapeHtml(phaseLabels[stage.phase]) + '</h2>' + statusPill(stage.status) +
            '<p class="og-detail-summary">' + escapeHtml(stage.summary || '本阶段尚未开始。') + '</p><dl><dt>角色</dt><dd>' + escapeHtml(display(stage.roleName) || '未分配') + '</dd><dt>服务商</dt><dd>' + escapeHtml(stage.providerName || '—') +
            '</dd><dt>模型</dt><dd>' + escapeHtml(stage.modelName || '—') + '</dd><dt>更新时间</dt><dd>' + escapeHtml(formatTime(stage.updatedAt)) + '</dd></dl>' +
            '<section><h3>使用的工具</h3><div class="og-tool-chips">' + tools + '</div></section><section><h3>产物</h3><div class="og-stage-artifact">' + artifact + '</div></section>' +
            '<p class="og-private-note">这里只展示路由事实和执行元数据，不会展示模型的私有推理过程。</p></aside>';
    }

    function eventRow(event) {
        var payload = event.payload || {};
        var metadata = [];
        if (payload.toolName) metadata.push('工具：' + payload.toolName);
        if (payload.exitCode != null) metadata.push('退出码：' + payload.exitCode);
        if (payload.durationMs != null) metadata.push(formatDuration(payload.durationMs));
        if (payload.inputTokens != null || payload.outputTokens != null) metadata.push('Token：输入 ' + Number(payload.inputTokens || 0).toLocaleString('zh-CN') + ' / 输出 ' + Number(payload.outputTokens || 0).toLocaleString('zh-CN'));
        return '<article class="og-event-row"><div class="og-event-dot"></div><div><div class="og-event-head"><b>' + escapeHtml(display(event.eventType)) + '</b><time>' + escapeHtml(formatTime(event.timestamp)) + '</time></div>' +
            '<p>' + escapeHtml(payload.summary || payload.message || '已记录执行元数据。') + '</p><small>' + escapeHtml([event.providerName, event.modelName].filter(Boolean).join(' · ') || '系统事件') + '</small>' +
            (metadata.length ? '<div class="og-event-metadata">' + metadata.map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join('') + '</div>' : '') + '</div></article>';
    }

    function drawer(run) {
        return '<div class="og-drawer-backdrop' + (state.drawerOpen ? ' open' : '') + '" id="execution-backdrop"></div><aside class="og-execution-drawer' + (state.drawerOpen ? ' open' : '') + '" aria-label="高级执行详情">' +
            '<header><div><span>高级</span><h2>执行详情</h2><p>' + escapeHtml(run.runId) + '</p></div><button id="close-execution-drawer" aria-label="关闭">×</button></header>' +
            '<div class="og-drawer-notice">仅展示产品安全的事件元数据，不包含提示词、模型原始输出、凭证和私有推理。</div><div class="og-event-list">' +
            (state.events.length ? state.events.map(eventRow).join('') : '<div class="og-drawer-loading">打开抽屉后加载事件。</div>') + '</div></aside>';
    }

    function render() {
        var run = state.runs.find(function (item) { return item.runId === state.selectedRunId; });
        if (!run) {
            root.innerHTML = header() + '<div class="og-workspace-content">' + emptyState('暂无工作流运行', '结构化工作流启动后，事件会显示在这里。') + '</div>';
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
            history.replaceState(null, '', '?runId=' + encodeURIComponent(state.selectedRunId));
            render();
            connectStream();
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

    async function refreshSelectedRun() {
        if (!state.selectedRunId) return;
        try {
            var result = await api.getWorkflowRun(state.selectedRunId);
            var index = state.runs.findIndex(function (item) { return item.runId === state.selectedRunId; });
            if (index === -1) state.runs.unshift(result.run); else state.runs[index] = result.run;
            if (state.drawerOpen) {
                var eventResult = await api.listWorkflowEvents(state.selectedRunId);
                state.events = eventResult.items || [];
            }
            render();
            if (isTerminal(result.run) && state.stream) {
                state.stream.close();
                state.stream = null;
            }
        } catch (_error) {
            // EventSource will retry transient failures; keep the last safe projection.
        }
    }

    function scheduleRefresh() {
        if (state.refreshTimer) return;
        state.refreshTimer = window.setTimeout(function () {
            state.refreshTimer = null;
            refreshSelectedRun();
        }, 150);
    }

    function connectStream() {
        if (state.stream) state.stream.close();
        state.stream = null;
        var run = state.runs.find(function (item) { return item.runId === state.selectedRunId; });
        if (!run || isTerminal(run) || !window.EventSource) return;
        state.stream = new EventSource(api.workflowEventStreamUrl(run.runId));
        state.stream.onmessage = scheduleRefresh;
        state.stream.onerror = scheduleRefresh;
    }

    async function mount() {
        root.innerHTML = header() + '<div class="og-workspace-content"><div class="og-loading-state">正在加载工作流时间线…</div></div>';
        var params = new URLSearchParams(window.location.search);
        try {
            var result = await api.listWorkflowRuns({projectId: params.get('projectId'), taskId: params.get('taskId')});
            state.runs = result.items || [];
            state.selectedRunId = params.get('runId') || (state.runs[0] && state.runs[0].runId) || '';
            render();
            connectStream();
        } catch (error) {
            root.innerHTML = header() + '<div class="og-workspace-content">' + emptyState('工作流时间线不可用', error.message) + '</div>';
        }
    }

    mount();
    window.addEventListener('beforeunload', function () {
        if (state.stream) state.stream.close();
    });
})();
