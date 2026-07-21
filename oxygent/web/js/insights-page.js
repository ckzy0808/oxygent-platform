(function () {
    'use strict';

    var api = window.OxyGentApp.api;
    var root = document.getElementById('insights-page');
    var tabs = ['Overview', 'Usage', 'Cost', 'Reliability'];
    var state = {
        tab: 'overview',
        range: '30d',
        projectId: '',
        projects: [],
        summary: {totals: {}, budgets: []},
        breakdown: [],
        runs: [],
        loading: true,
        error: ''
    };

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function number(value) {
        return Number(value || 0).toLocaleString();
    }

    function cost(value) {
        return '$' + Number(value || 0).toFixed(4);
    }

    function percent(value) {
        return value == null ? '—' : (Number(value) * 100).toFixed(1) + '%';
    }

    function duration(value) {
        return Math.round(Number(value || 0)).toLocaleString() + ' ms';
    }

    function filters() {
        var result = {};
        if (state.projectId) result.projectId = state.projectId;
        var now = new Date();
        if (state.range === '7d' || state.range === '30d') {
            var days = state.range === '7d' ? 7 : 30;
            result.dateFrom = new Date(now.getTime() - days * 86400000).toISOString();
            result.dateTo = now.toISOString();
        } else if (state.range === 'month') {
            result.dateFrom = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString();
            result.dateTo = now.toISOString();
        }
        return result;
    }

    function dimension() {
        if (state.tab === 'usage') return 'model';
        if (state.tab === 'cost') return 'provider';
        if (state.tab === 'reliability') return 'status';
        return 'project';
    }

    function header() {
        return '<header class="og-workspace-header og-insights-header"><div><p class="og-workspace-eyebrow">Operational intelligence</p>' +
            '<h1 class="og-workspace-title">Insights</h1><p>Usage, estimated cost, reliability, and routing outcomes from recorded model invocations.</p></div>' +
            '<div class="og-insights-filters"><label>Project<select id="insights-project"><option value="">All Projects</option>' +
            state.projects.map(function (project) { return '<option value="' + escapeHtml(project.id) + '"' + (project.id === state.projectId ? ' selected' : '') + '>' + escapeHtml(project.name) + '</option>'; }).join('') +
            '</select></label><label>Range<select id="insights-range"><option value="7d"' + selected('7d', state.range) + '>Last 7 days</option>' +
            '<option value="30d"' + selected('30d', state.range) + '>Last 30 days</option><option value="month"' + selected('month', state.range) + '>This month</option>' +
            '<option value="all"' + selected('all', state.range) + '>All time</option></select></label></div></header>';
    }

    function selected(value, current) {
        return value === current ? ' selected' : '';
    }

    function metricCards() {
        var totals = state.summary.totals || {};
        return '<section class="og-insight-metrics" aria-label="Usage summary">' +
            metric('Invocations', number(totals.invocations), number(totals.fallbackInvocations) + ' fallback') +
            metric('Tokens', number(totals.totalTokens), number(totals.inputTokens) + ' in · ' + number(totals.outputTokens) + ' out') +
            metric('Estimated cost', cost(totals.estimatedCost), percent(totals.costCoverage) + ' price coverage') +
            metric('Success rate', percent(totals.successRate), number(totals.failed) + ' failed') +
            metric('P95 latency', duration(totals.p95LatencyMs), duration(totals.averageLatencyMs) + ' average') + '</section>';
    }

    function metric(label, value, note) {
        return '<article><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value) + '</strong><small>' + escapeHtml(note) + '</small></article>';
    }

    function budgetPanel() {
        var budgets = state.summary.budgets || [];
        if (!budgets.length) return empty('No Project budgets', 'Set settings.monthlyBudget on a Project to track monthly estimated spend.');
        return '<section class="og-insight-panel"><div class="og-panel-heading"><div><span>Monthly budgets</span><h2>Estimated spend guardrails</h2></div>' +
            '<small>Warnings are observational and do not change routing policy.</small></div><div class="og-budget-list">' + budgets.map(function (item) {
                var width = item.percentUsed == null ? 0 : Math.min(100, Math.max(0, Number(item.percentUsed) * 100));
                return '<article><div><b>' + escapeHtml(item.projectName) + '</b><span class="og-budget-status ' + escapeHtml(item.status) + '">' + escapeHtml(item.status) + '</span></div>' +
                    '<p><strong>' + cost(item.currentSpend) + '</strong> / ' + (item.monthlyBudget == null ? 'Not configured' : cost(item.monthlyBudget)) + '</p>' +
                    '<div class="og-budget-track"><span style="width:' + width.toFixed(1) + '%"></span></div><small>' +
                    (item.unpricedInvocations ? number(item.unpricedInvocations) + ' invocation(s) have unavailable pricing' : percent(item.percentUsed) + ' used') + '</small></article>';
            }).join('') + '</div></section>';
    }

    function breakdownTable() {
        if (!state.breakdown.length) return empty('No usage in this range', 'Recorded model calls will appear here after the selected filters match them.');
        var max = Math.max.apply(null, state.breakdown.map(function (row) { return row.totals.estimatedCost || row.totals.invocations || 0; })) || 1;
        return '<section class="og-insight-panel"><div class="og-panel-heading"><div><span>Breakdown</span><h2>By ' + escapeHtml(dimension()) + '</h2></div><small>Append-only Model Usage records</small></div>' +
            '<div class="og-insight-table"><div class="og-insight-row head"><span>Name</span><span>Calls</span><span>Tokens</span><span>Cost</span><span>Success</span><span>Latency</span></div>' +
            state.breakdown.map(function (row) {
                var totals = row.totals;
                var base = totals.estimatedCost || totals.invocations || 0;
                return '<article class="og-insight-row"><div><b>' + escapeHtml(row.label) + '</b><div class="og-mini-bar"><span style="width:' + Math.max(2, base / max * 100).toFixed(1) + '%"></span></div></div>' +
                    '<span>' + number(totals.invocations) + '</span><span>' + number(totals.totalTokens) + '</span><span>' + cost(totals.estimatedCost) +
                    (totals.unpricedInvocations ? '<small>' + number(totals.unpricedInvocations) + ' unpriced</small>' : '') + '</span><span>' + percent(totals.successRate) + '</span><span>' + duration(totals.averageLatencyMs) + '</span></article>';
            }).join('') + '</div></section>';
    }

    function recentRuns() {
        if (!state.runs.length) return '';
        return '<section class="og-insight-panel"><div class="og-panel-heading"><div><span>Recent invocations</span><h2>Routing and run links</h2></div>' +
            '<small>Policy reasons only; private model reasoning is never displayed.</small></div><div class="og-run-list">' + state.runs.map(function (run) {
                return '<article><div class="og-run-status ' + escapeHtml(run.status) + '"></div><div><b>' + escapeHtml(run.roleId) + ' · ' + escapeHtml(run.modelId) + '</b>' +
                    '<p>' + escapeHtml(run.selectionReason) + '</p><small>' + escapeHtml(run.providerId) + ' · ' + number(run.inputTokens + run.outputTokens) + ' tokens · ' + cost(run.estimatedCost) + '</small></div>' +
                    '<a href="' + escapeHtml(run.workflowUrl) + '">Open run</a></article>';
            }).join('') + '</div></section>';
    }

    function empty(title, message) {
        return '<section class="og-insight-empty"><div>◇</div><h2>' + escapeHtml(title) + '</h2><p>' + escapeHtml(message) + '</p></section>';
    }

    function render() {
        var tabsHtml = '<nav class="og-section-tabs" aria-label="Insight sections">' + tabs.map(function (tab) {
            var id = tab.toLowerCase();
            return '<button class="og-section-tab' + (state.tab === id ? ' active' : '') + '" data-insights-tab="' + id + '">' + tab + '</button>';
        }).join('') + '</nav>';
        var body;
        if (state.loading) body = '<div class="og-loading-state">Aggregating recorded model usage…</div>';
        else if (state.error) body = empty('Insights unavailable', state.error);
        else body = metricCards() + (state.tab === 'cost' || state.tab === 'overview' ? budgetPanel() : '') + breakdownTable() + recentRuns();
        root.innerHTML = header() + '<div class="og-workspace-content">' + tabsHtml + body + '</div>';
        bind();
    }

    function bind() {
        root.querySelectorAll('[data-insights-tab]').forEach(function (button) {
            button.addEventListener('click', function () {
                state.tab = button.getAttribute('data-insights-tab');
                history.replaceState(null, '', '?tab=' + encodeURIComponent(state.tab));
                loadInsights();
            });
        });
        var project = document.getElementById('insights-project');
        var range = document.getElementById('insights-range');
        if (project) project.addEventListener('change', function () { state.projectId = project.value; loadInsights(); });
        if (range) range.addEventListener('change', function () { state.range = range.value; loadInsights(); });
    }

    async function loadInsights() {
        state.loading = true;
        state.error = '';
        render();
        try {
            var query = filters();
            var results = await Promise.all([
                api.getInsightsSummary(query),
                api.getInsightsBreakdown(dimension(), query),
                api.listInsightRuns(Object.assign({limit: 12}, query))
            ]);
            state.summary = results[0];
            state.breakdown = results[1].items || [];
            state.runs = results[2].items || [];
        } catch (error) {
            state.error = error.message;
        } finally {
            state.loading = false;
            render();
        }
    }

    async function mount() {
        state.tab = new URLSearchParams(window.location.search).get('tab') || 'overview';
        render();
        try {
            state.projects = (await api.listProjects()).items || [];
        } catch (error) {
            state.error = error.message;
        }
        await loadInsights();
    }

    mount();
})();
