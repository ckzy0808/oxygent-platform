(function () {
    'use strict';

    var api = window.OxyGentApp.api;
    var root = document.getElementById('agents-page');

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function percentage(value) {
        return value == null ? 'No data' : Math.round(value * 100) + '%';
    }

    function cost(value) {
        return '$' + Number(value || 0).toFixed(4);
    }

    function header() {
        return '<header class="og-workspace-header"><div><p class="og-workspace-eyebrow">Agent registry</p><h1 class="og-workspace-title">Agent Team</h1>' +
            '<p class="og-header-detail">Role, Agent, Provider, model policy, tools, and usage remain explicit and independently configurable.</p></div>' +
            '<span class="og-preview-badge">Role mapped</span></header>';
    }

    function render(agents) {
        root.innerHTML = header() + '<div class="og-workspace-content">' +
            '<section class="og-assignment-strip" aria-label="Role to model assignments">' + agents.map(function (agent) {
                return '<article><div class="og-role-symbol">' + escapeHtml(agent.role.name.charAt(0)) + '</div><div><span>' + escapeHtml(agent.role.name) + '</span>' +
                    '<strong>' + escapeHtml(agent.model.displayName) + '</strong><small>' + escapeHtml(agent.provider.name) + '</small></div><b>→</b></article>';
            }).join('') + '</section>' +
            '<div class="og-control-toolbar"><div><strong>' + agents.length + '</strong> configured Agents</div><span>Private model reasoning is never displayed</span></div>' +
            (agents.length ? '<div class="og-agent-table"><div class="og-agent-row og-agent-head"><span>Role / Agent</span><span>Provider / Model</span><span>Routing</span><span>Capabilities</span><span>Tool policy</span><span>Status</span><span>Usage / Cost</span><span>Success</span></div>' +
                agents.map(function (agent) { return '<article class="og-agent-row"><div class="og-agent-identity"><span class="og-role-symbol">' + escapeHtml(agent.role.name.charAt(0)) + '</span><div><b>' + escapeHtml(agent.role.name) + '</b><small>' + escapeHtml(agent.agentName) + '</small></div></div>' +
                    '<div class="og-provider-model"><b>' + escapeHtml(agent.provider.name) + '</b><small>' + escapeHtml(agent.model.displayName) + '</small></div>' +
                    '<div><span class="og-routing-pill og-routing-' + agent.routingState.toLowerCase() + '">' + escapeHtml(agent.routingState) + '</span><small class="og-cell-note">' + escapeHtml(agent.routingMode) + '</small></div>' +
                    '<div class="og-chip-list">' + agent.capabilities.map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join('') + '</div>' +
                    '<div><b class="og-cell-value">' + escapeHtml(agent.toolPolicy.name) + '</b><small class="og-cell-note">' + agent.toolPolicy.allowedTools.length + ' allowed</small></div>' +
                    '<div><span class="og-health-pill og-health-' + agent.currentStatus.toLowerCase() + '">' + escapeHtml(agent.currentStatus) + '</span></div>' +
                    '<div><b class="og-cell-value">' + (agent.usage.inputTokens + agent.usage.outputTokens).toLocaleString() + ' tokens</b><small class="og-cell-note">' + cost(agent.usage.estimatedCost) + '</small></div>' +
                    '<div><b class="og-cell-value">' + percentage(agent.usage.successRate) + '</b><small class="og-cell-note">' + agent.usage.invocations + ' calls</small></div>' +
                    '<details class="og-route-reason"><summary>Why this model?</summary><p>' + escapeHtml(agent.selectionReason) + '</p><div><b>Primary:</b> ' + agent.primaryModels.map(function (model) { return escapeHtml(model.providerName + ' / ' + model.displayName); }).join(', ') +
                    '</div><div><b>Fallback:</b> ' + (agent.fallbackModels.length ? agent.fallbackModels.map(function (model) { return escapeHtml(model.providerName + ' / ' + model.displayName); }).join(', ') : 'None') + '</div></details></article>'; }).join('') + '</div>' :
                '<section class="og-project-empty"><div class="og-empty-icon">◇</div><h2>No Agent Profiles configured</h2><p>Start OxyGent with a populated PlatformControlPlane to see role assignments.</p></section>') + '</div>';
    }

    function renderError(error) {
        root.innerHTML = header() + '<div class="og-workspace-content"><section class="og-project-empty"><h2>Agent control plane unavailable</h2><p>' + escapeHtml(error.message) + '</p></section></div>';
    }

    async function mount() {
        root.innerHTML = header() + '<div class="og-workspace-content"><div class="og-loading-state">Loading Agent Team…</div></div>';
        try {
            var result = await api.listAgents();
            render(result.items || []);
        } catch (error) {
            renderError(error);
        }
    }

    mount();
})();
