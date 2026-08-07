(function () {
    'use strict';

    var api = window.OxyGentApp.api;
    var root = document.getElementById('insights-page');
    var tabs = [{id: 'overview', label: '概览'}, {id: 'usage', label: '用量'}, {id: 'reliability', label: '可靠性'}];
    var displayLabels = {project: '项目', model: '模型', provider: '服务商', status: '状态', healthy: '健康', warning: '警告', exceeded: '已超支', unavailable: '不可用', product_manager: '产品经理', solution_architect: '解决方案架构师', technical_lead: '技术负责人', reviewer: '审查员', succeeded: '成功', failed: '失败'};
    var state = {
        tab: 'overview',
        range: '30d',
        projectId: '',
        projects: [],
        summary: {totals: {}},
        breakdown: [],
        runs: [],
        refreshTimer: null,
        loading: true,
        error: ''
    };

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function number(value) {
        return Number(value || 0).toLocaleString('zh-CN');
    }

    function percent(value) {
        return value == null ? '—' : (Number(value) * 100).toFixed(1) + '%';
    }

    function duration(value) {
        return Math.round(Number(value || 0)).toLocaleString('zh-CN') + ' 毫秒';
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
        if (state.tab === 'reliability') return 'status';
        return 'project';
    }

    function header() {
        return '<header class="og-workspace-header og-insights-header"><div><p class="og-workspace-eyebrow">运营洞察</p>' +
            '<h1 class="og-workspace-title">洞察</h1><p>基于每次模型 API 的真实返回，实时查看 Token、计量方式、可靠性和路由结果。</p></div>' +
            '<div class="og-insights-filters"><label>项目<select id="insights-project"><option value="">全部项目</option>' +
            state.projects.map(function (project) { return '<option value="' + escapeHtml(project.id) + '"' + (project.id === state.projectId ? ' selected' : '') + '>' + escapeHtml(project.name) + '</option>'; }).join('') +
            '</select></label><label>时间范围<select id="insights-range"><option value="7d"' + selected('7d', state.range) + '>最近 7 天</option>' +
            '<option value="30d"' + selected('30d', state.range) + '>最近 30 天</option><option value="month"' + selected('month', state.range) + '>本月</option>' +
            '<option value="all"' + selected('all', state.range) + '>全部时间</option></select></label></div></header>';
    }

    function selected(value, current) {
        return value === current ? ' selected' : '';
    }

    function metricCards() {
        var totals = state.summary.totals || {};
        return '<section class="og-insight-metrics" aria-label="用量摘要">' +
            metric('调用次数', number(totals.invocations), number(totals.fallbackInvocations) + ' 次备用切换') +
            metric('Token', number(totals.totalTokens), '输入 ' + number(totals.inputTokens) + ' · 输出 ' + number(totals.outputTokens)) +
            metric('精确计量', number(totals.exactInvocations), number(totals.estimatedInvocations) + ' 次为估算值') +
            metric('成功率', percent(totals.successRate), number(totals.failed) + ' 次失败') +
            metric('P95 延迟', duration(totals.p95LatencyMs), '平均 ' + duration(totals.averageLatencyMs)) + '</section>';
    }

    function metric(label, value, note) {
        return '<article><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value) + '</strong><small>' + escapeHtml(note) + '</small></article>';
    }

    function breakdownTable() {
        if (!state.breakdown.length) return empty('所选范围内暂无用量', '符合筛选条件的模型调用记录会显示在这里。');
        var max = Math.max.apply(null, state.breakdown.map(function (row) { return row.totals.totalTokens || 0; })) || 1;
        return '<section class="og-insight-panel"><div class="og-panel-heading"><div><span>明细</span><h2>按' + escapeHtml(displayLabels[dimension()] || dimension()) + '统计</h2></div><small>仅追加的模型用量记录</small></div>' +
            '<div class="og-insight-table"><div class="og-insight-row head"><span>名称</span><span>调用</span><span>Token</span><span>精确 / 估算</span><span>成功率</span><span>延迟</span></div>' +
            state.breakdown.map(function (row) {
                var totals = row.totals;
                var base = totals.totalTokens || 0;
                return '<article class="og-insight-row"><div><b>' + escapeHtml(row.label) + '</b><div class="og-mini-bar"><span style="width:' + Math.max(2, base / max * 100).toFixed(1) + '%"></span></div></div>' +
                    '<span>' + number(totals.invocations) + '</span><span>' + number(totals.totalTokens) + '</span><span>' + number(totals.exactInvocations) + ' / ' + number(totals.estimatedInvocations) + '</span><span>' + percent(totals.successRate) + '</span><span>' + duration(totals.averageLatencyMs) + '</span></article>';
            }).join('') + '</div></section>';
    }

    function recentRuns() {
        if (!state.runs.length) return '';
        return '<section class="og-insight-panel"><div class="og-panel-heading"><div><span>最近调用</span><h2>路由与运行链接</h2></div>' +
            '<small>只展示策略原因，不会展示模型的私有推理过程。</small></div><div class="og-run-list">' + state.runs.map(function (run) {
                return '<article><div class="og-run-status ' + escapeHtml(run.status) + '"></div><div><b>' + escapeHtml(displayLabels[run.roleId] || run.roleId) + ' · ' + escapeHtml(run.modelId) + '</b>' +
                    '<p>' + escapeHtml(run.selectionReason) + '</p><small>' + escapeHtml(run.providerId) + ' · 输入 ' + number(run.inputTokens) + ' / 输出 ' + number(run.outputTokens) + ' Token · ' + (run.tokenCountMethod === 'exact' ? 'API 精确返回' : '估算') + '</small></div>' +
                    '<a href="' + escapeHtml(run.workflowUrl) + '">打开运行</a></article>';
            }).join('') + '</div></section>';
    }

    function empty(title, message) {
        return '<section class="og-insight-empty"><div>◇</div><h2>' + escapeHtml(title) + '</h2><p>' + escapeHtml(message) + '</p></section>';
    }

    function render() {
        var tabsHtml = '<nav class="og-section-tabs" aria-label="洞察栏目">' + tabs.map(function (tab) {
            return '<button class="og-section-tab' + (state.tab === tab.id ? ' active' : '') + '" data-insights-tab="' + tab.id + '">' + tab.label + '</button>';
        }).join('') + '</nav>';
        var body;
        if (state.loading) body = '<div class="og-loading-state">正在汇总模型用量记录…</div>';
        else if (state.error) body = empty('洞察不可用', state.error);
        else body = metricCards() + breakdownTable() + recentRuns();
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

    async function loadInsights(silent) {
        window.clearTimeout(state.refreshTimer);
        state.loading = !silent;
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
            window.clearTimeout(state.refreshTimer);
            state.refreshTimer = window.setTimeout(function () { loadInsights(true); }, 2000);
        }
    }

    async function mount() {
        state.tab = new URLSearchParams(window.location.search).get('tab') || 'overview';
        if (!tabs.some(function (tab) { return tab.id === state.tab; })) state.tab = 'overview';
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
