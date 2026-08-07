(function () {
    'use strict';

    var api = window.OxyGentApp.api;
    var root = document.getElementById('agents-page');
    var refreshTimer = null;
    var labels = {
        product_manager: '产品经理', solution_architect: '解决方案架构师', technical_lead: '技术负责人', reviewer: '审查员',
        'Product Manager': '产品经理', 'Solution Architect': '解决方案架构师', 'Technical Lead': '技术负责人', Reviewer: '审查员',
        Auto: '自动', Fixed: '固定', Fallback: '备用', Ready: '就绪', priority: '优先级', healthy: '健康', degraded: '性能下降', unavailable: '不可用',
        reasoning: '推理', structured_output: '结构化输出', 'structured-output': '结构化输出', tool_calling: '工具调用', 'tool-calling': '工具调用',
        'long-context': '长上下文', text: '文本', review: '审查', vision: '视觉', code: '代码'
    };

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function percentage(value) {
        return value == null ? '暂无数据' : Math.round(value * 100) + '%';
    }

    function label(value) {
        return labels[value] || labels[String(value || '').toLowerCase()] || value;
    }

    function header() {
        return '<header class="og-workspace-header"><div><p class="og-workspace-eyebrow">智能体注册表</p><h1 class="og-workspace-title">智能体团队</h1>' +
            '<p class="og-header-detail">角色、智能体、服务商、模型策略、工具和用量均清晰展示，并可独立配置。</p></div>' +
            '<span class="og-preview-badge">已映射角色</span></header>';
    }

    function render(agents) {
        root.innerHTML = header() + '<div class="og-workspace-content">' +
            '<section class="og-assignment-strip" aria-label="角色与模型分配">' + agents.map(function (agent) {
                return '<article><div class="og-role-symbol">' + escapeHtml(label(agent.role.name).charAt(0)) + '</div><div><span>' + escapeHtml(label(agent.role.id) || label(agent.role.name)) + '</span>' +
                    '<strong>' + escapeHtml(agent.model.displayName) + '</strong><small>' + escapeHtml(agent.provider.name) + '</small></div><b>→</b></article>';
            }).join('') + '</section>' +
            '<div class="og-control-toolbar"><div>已配置 <strong>' + agents.length + '</strong> 个智能体</div><span>不会展示模型的私有推理过程</span></div>' +
            (agents.length ? '<div class="og-agent-table"><div class="og-agent-row og-agent-head"><span>角色 / 智能体</span><span>服务商 / 模型</span><span>路由</span><span>能力</span><span>工具策略</span><span>状态</span><span>Token 用量</span><span>成功率</span></div>' +
                agents.map(function (agent) { return '<article class="og-agent-row"><div class="og-agent-identity"><span class="og-role-symbol">' + escapeHtml(label(agent.role.name).charAt(0)) + '</span><div><b>' + escapeHtml(label(agent.role.id) || label(agent.role.name)) + '</b><small>' + escapeHtml(agent.agentName) + '</small></div></div>' +
                    '<div class="og-provider-model"><b>' + escapeHtml(agent.provider.name) + '</b><small>' + escapeHtml(agent.model.displayName) + '</small></div>' +
                    '<div><span class="og-routing-pill og-routing-' + agent.routingState.toLowerCase() + '">' + escapeHtml(label(agent.routingState)) + '</span><small class="og-cell-note">' + escapeHtml(label(agent.routingMode)) + '</small></div>' +
                    '<div class="og-chip-list">' + agent.capabilities.map(function (item) { return '<span>' + escapeHtml(label(item)) + '</span>'; }).join('') + '</div>' +
                    '<div><b class="og-cell-value">' + escapeHtml(agent.toolPolicy.name) + '</b><small class="og-cell-note">允许 ' + agent.toolPolicy.allowedTools.length + ' 个工具</small></div>' +
                    '<div><span class="og-health-pill og-health-' + agent.currentStatus.toLowerCase() + '">' + escapeHtml(label(agent.currentStatus)) + '</span></div>' +
                    '<div><b class="og-cell-value">' + (agent.usage.inputTokens + agent.usage.outputTokens).toLocaleString() + ' Token</b><small class="og-cell-note">输入 ' + agent.usage.inputTokens.toLocaleString() + ' · 输出 ' + agent.usage.outputTokens.toLocaleString() + '</small></div>' +
                    '<div><b class="og-cell-value">' + percentage(agent.usage.successRate) + '</b><small class="og-cell-note">' + agent.usage.invocations + ' 次调用</small></div>' +
                    '<details class="og-route-reason"><summary>为何选择此模型？</summary><p>' + escapeHtml(agent.selectionReason) + '</p><div><b>主模型：</b> ' + agent.primaryModels.map(function (model) { return escapeHtml(model.providerName + ' / ' + model.displayName); }).join(', ') +
                    '</div><div><b>备用模型：</b> ' + (agent.fallbackModels.length ? agent.fallbackModels.map(function (model) { return escapeHtml(model.providerName + ' / ' + model.displayName); }).join(', ') : '无') + '</div></details></article>'; }).join('') + '</div>' :
                '<section class="og-project-empty"><div class="og-empty-icon">◇</div><h2>尚未配置智能体档案</h2><p>请使用已填充的平台控制平面启动 OxyGent，以查看角色分配。</p></section>') + '</div>';
    }

    function renderError(error) {
        root.innerHTML = header() + '<div class="og-workspace-content"><section class="og-project-empty"><h2>智能体控制平面不可用</h2><p>' + escapeHtml(error.message) + '</p></section></div>';
    }

    async function mount() {
        window.clearTimeout(refreshTimer);
        root.innerHTML = header() + '<div class="og-workspace-content"><div class="og-loading-state">正在加载智能体团队…</div></div>';
        try {
            var result = await api.listAgents();
            render(result.items || []);
            refreshTimer = window.setTimeout(mount, 2000);
        } catch (error) {
            renderError(error);
        }
    }

    mount();
})();
