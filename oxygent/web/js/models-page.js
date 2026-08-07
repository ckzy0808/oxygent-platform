(function () {
    'use strict';

    var api = window.OxyGentApp.api;
    var root = document.getElementById('models-page');
    var state = {tab: 'providers', capabilities: {}, providers: [], models: [], policies: [], usage: [], usageSummary: {}, refreshTimer: null};
    var tabs = [{id: 'providers', label: '服务商'}, {id: 'models', label: '模型'}, {id: 'routing-policies', label: '路由策略'}, {id: 'usage', label: '用量'}];
    var labels = {
        healthy: '健康', degraded: '性能下降', unavailable: '不可用', unknown: '未知', disabled: '已禁用',
        succeeded: '成功', failed: '失败', priority: '优先级', fixed: '固定', auto: '自动',
        'product-manager': '产品经理', 'solution-architect': '解决方案架构师', 'technical-lead': '技术负责人',
        product_manager: '产品经理', solution_architect: '解决方案架构师', technical_lead: '技术负责人', reviewer: '审查员',
        'Product Manager': '产品经理', 'Solution Architect': '解决方案架构师', 'Technical Lead': '技术负责人', Reviewer: '审查员',
        reasoning: '推理', structured_output: '结构化输出', 'structured-output': '结构化输出', tool_calling: '工具调用', 'tool-calling': '工具调用',
        'long-context': '长上下文', text: '文本', review: '审查', vision: '视觉', code: '代码'
    };

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function formatNumber(value) {
        return Number(value || 0).toLocaleString('zh-CN');
    }

    function displayLabel(value) {
        return labels[value] || labels[String(value || '').toLowerCase()] || value;
    }

    function header() {
        var addDisabled = state.capabilities.providerMutations ? '' : ' disabled title="服务器策略已禁用服务商修改"';
        return '<header class="og-workspace-header"><div><p class="og-workspace-eyebrow">模型控制平面</p><h1 class="og-workspace-title">模型</h1>' +
            '<p class="og-header-detail">统一管理服务商、模型、路由策略、健康状态和用量，且不会暴露已解析的凭证。</p></div>' +
            '<button class="og-primary-button" id="add-provider-button"' + addDisabled + '>添加服务商</button></header>';
    }

    function shell(content) {
        root.innerHTML = header() + '<div class="og-workspace-content"><nav class="og-section-tabs" aria-label="模型栏目">' + tabs.map(function (tab) {
            return '<button class="og-section-tab' + (state.tab === tab.id ? ' active' : '') + '" data-model-tab="' + tab.id + '">' + tab.label + '</button>';
        }).join('') + '</nav><div id="model-tab-content">' + content + '</div></div>' + providerModal();
        bindNavigation();
    }

    function providerModal() {
        return '<div class="og-modal-backdrop" id="provider-modal" hidden><section class="og-modal" role="dialog" aria-modal="true" aria-labelledby="provider-modal-title">' +
            '<div class="og-modal-header"><div><p class="og-workspace-eyebrow">凭证安全配置</p><h2 id="provider-modal-title">添加服务商</h2></div>' +
            '<button type="button" class="og-icon-button" data-close-provider aria-label="关闭">×</button></div><form id="provider-form">' +
            '<input type="hidden" name="editingId"><label>服务商 ID<input name="id" required maxlength="120" pattern="[a-zA-Z0-9_.-]+" placeholder="provider-id"></label>' +
            '<label>名称<input name="name" required maxlength="160" placeholder="服务商显示名称"></label><label>协议<select name="providerType">' +
            '<option value="openai-compatible">OpenAI-compatible</option><option value="gemini">Gemini</option><option value="ollama">Ollama</option></select></label>' +
            '<label>基础 URL<input name="baseUrl" type="url" required maxlength="1000" placeholder="https://provider.example/v1"></label>' +
            '<label>凭证引用<input name="credentialReference" maxlength="300" placeholder="env:PROVIDER_API_KEY"><small>请输入密钥引用，切勿填写 API Key 的实际值。</small></label>' +
            '<label>超时时间（秒）<input name="timeout" type="number" min="1" max="3600" value="120" required></label>' +
            '<p class="og-form-error" id="provider-form-error" role="alert"></p><div class="og-modal-actions"><button type="button" class="og-secondary-button" data-close-provider>取消</button>' +
            '<button type="submit" class="og-primary-button">保存服务商</button></div></form></section></div>';
    }

    function bindNavigation() {
        root.querySelectorAll('[data-model-tab]').forEach(function (button) {
            button.addEventListener('click', function () {
                state.tab = button.getAttribute('data-model-tab');
                history.replaceState(null, '', '?tab=' + encodeURIComponent(state.tab));
                render();
                refreshUsage();
            });
        });
        var add = document.getElementById('add-provider-button');
        if (add && !add.disabled) add.addEventListener('click', function () { openProviderModal(); });
        root.querySelectorAll('[data-close-provider]').forEach(function (button) {
            button.addEventListener('click', function () { document.getElementById('provider-modal').hidden = true; });
        });
        var form = document.getElementById('provider-form');
        if (form) form.addEventListener('submit', saveProvider);
        root.querySelectorAll('[data-edit-provider]').forEach(function (button) {
            button.addEventListener('click', function () { openProviderModal(button.getAttribute('data-edit-provider')); });
        });
        root.querySelectorAll('[data-test-provider]').forEach(function (button) {
            button.addEventListener('click', function () { testProvider(button); });
        });
        root.querySelectorAll('[data-toggle-provider]').forEach(function (button) {
            button.addEventListener('click', function () { toggleProvider(button); });
        });
    }

    function render() {
        if (state.tab === 'models') shell(renderModels());
        else if (state.tab === 'routing-policies') shell(renderPolicies());
        else if (state.tab === 'usage') shell(renderUsage());
        else shell(renderProviders());
    }

    function renderProviders() {
        if (!state.providers.length) return emptyState('尚未配置服务商', '请添加凭证引用并选择服务商协议。');
        return '<div class="og-control-toolbar"><div><strong>' + state.providers.length + '</strong> 个服务商</div><span>' +
            (state.capabilities.providerMutations ? '已启用本地服务商管理' : '服务商管理为只读') + '</span></div>' +
            '<div class="og-provider-grid">' + state.providers.map(function (provider) {
                var providerModels = state.models.filter(function (model) { return model.providerId === provider.id; });
                var disabled = state.capabilities.providerMutations ? '' : ' disabled';
                var testDisabled = !state.capabilities.providerMutations || !providerModels.length ? ' disabled' : '';
                return '<article><div class="og-provider-card-head"><div class="og-provider-logo">' + escapeHtml(provider.name.charAt(0).toUpperCase()) + '</div><div><h2>' + escapeHtml(provider.name) + '</h2>' +
                    '<span>' + escapeHtml(provider.providerType) + '</span></div><span class="og-health-pill og-health-' + provider.healthStatus + '">' + escapeHtml(displayLabel(provider.healthStatus)) + '</span></div>' +
                    '<dl><dt>基础 URL</dt><dd title="' + escapeHtml(provider.baseUrl) + '">' + escapeHtml(provider.baseUrl) + '</dd><dt>凭证</dt><dd>' + escapeHtml(provider.credentialMask) + '</dd>' +
                    '<dt>引用</dt><dd><code>' + escapeHtml(provider.credentialReference || '未配置') + '</code></dd><dt>模型数</dt><dd>' + providerModels.length + '</dd></dl>' +
                    '<div class="og-provider-actions"><button class="og-secondary-button" data-edit-provider="' + escapeHtml(provider.id) + '"' + disabled + '>编辑</button>' +
                    '<button class="og-secondary-button" data-test-provider="' + escapeHtml(provider.id) + '"' + testDisabled + '>测试连接</button>' +
                    '<button class="og-text-button" data-toggle-provider="' + escapeHtml(provider.id) + '"' + disabled + '>' + (provider.enabled ? '禁用' : '启用') + '</button></div>' +
                    '<p class="og-connection-result" id="connection-' + escapeHtml(provider.id) + '"></p></article>';
            }).join('') + '</div>';
    }

    function renderModels() {
        if (!state.models.length) return emptyState('尚未注册模型', '请在平台控制平面中注册 ModelProfile 记录。');
        return '<div class="og-control-table"><div class="og-model-row og-control-head"><span>模型</span><span>服务商</span><span>能力</span><span>上下文</span><span>延迟</span><span>健康状态</span><span>分配角色</span></div>' +
            state.models.map(function (model) { return '<article class="og-model-row"><div><b>' + escapeHtml(model.displayName) + '</b><small>' + escapeHtml(model.modelName) + '</small></div><span>' + escapeHtml(model.providerName) + '</span>' +
                '<div class="og-chip-list">' + model.capabilities.map(function (item) { return '<span>' + escapeHtml(displayLabel(item)) + '</span>'; }).join('') + '</div><span>' + formatNumber(model.contextWindow) + '</span>' +
                '<span>等级 ' + model.latencyTier + '</span><span class="og-health-pill og-health-' + model.healthStatus + '">' + escapeHtml(displayLabel(model.healthStatus)) + '</span>' +
                '<span>' + escapeHtml(model.assignedRoles.map(displayLabel).join(', ') || '未分配') + '</span></article>'; }).join('') + '</div>';
    }

    function renderPolicies() {
        if (!state.policies.length) return emptyState('暂无路由策略', '请为每个智能体档案创建一项角色策略。');
        return '<div class="og-policy-grid">' + state.policies.map(function (policy) { return '<article><div class="og-policy-head"><div><span>角色</span><h2>' + escapeHtml(displayLabel(policy.role.id) || policy.role.name) + '</h2></div><span class="og-routing-pill og-routing-' +
            (policy.routingMode === 'priority' && policy.primaryModels.length === 1 ? 'fixed' : 'auto') + '">' + escapeHtml(displayLabel(policy.routingMode)) + '</span></div>' +
            '<div class="og-policy-chain"><div><b>主模型</b>' + policy.primaryModels.map(modelBadge).join('') + '</div><div><b>备用模型</b>' +
            (policy.fallbackModels.length ? policy.fallbackModels.map(modelBadge).join('') : '<span class="og-muted-copy">无</span>') + '</div></div>' +
            '<dl><dt>必需能力</dt><dd>' + escapeHtml(policy.requiredCapabilities.map(displayLabel).join(', ') || '无') + '</dd><dt>排除的服务商</dt><dd>' + escapeHtml(policy.excludedProviders.join(', ') || '无') +
            '</dd><dt>审查独立性</dt><dd>' + (policy.excludeSameProviderAsProducer ? '必须' : '不要求') + '</dd></dl></article>'; }).join('') + '</div>';
    }

    function modelBadge(model) {
        return '<span class="og-model-badge"><b>' + escapeHtml(model.displayName) + '</b><small>' + escapeHtml(model.providerName) + '</small></span>';
    }

    function renderUsage() {
        var summary = state.usageSummary;
        return '<div class="og-metric-grid"><article><span>调用次数</span><strong>' + formatNumber(summary.invocations) + '</strong></article><article><span>输入 Token</span><strong>' + formatNumber(summary.inputTokens) +
            '</strong></article><article><span>输出 Token</span><strong>' + formatNumber(summary.outputTokens) + '</strong></article><article><span>精确计量调用</span><strong>' + formatNumber(summary.exactInvocations) + '</strong></article></div>' +
            (state.usage.length ? '<div class="og-control-table"><div class="og-usage-row og-control-head"><span>角色</span><span>服务商 / 模型</span><span>输入 / 输出 Token</span><span>计量方式</span><span>延迟</span><span>状态</span><span>备用切换</span></div>' +
                state.usage.map(function (item) { var model = state.models.find(function (entry) { return entry.id === item.modelId; }); var provider = state.providers.find(function (entry) { return entry.id === item.providerId; }); return '<article class="og-usage-row"><span>' + escapeHtml(displayLabel(item.roleId)) + '</span><div><b>' + escapeHtml(provider ? provider.name : item.providerId) + '</b><small>' + escapeHtml(model ? model.displayName : item.modelId) +
                '</small></div><span>' + formatNumber(item.inputTokens) + ' / ' + formatNumber(item.outputTokens) + '</span><span>' + (item.tokenCountMethod === 'exact' ? 'API 精确返回' : '估算（' + escapeHtml(item.tokenCountMethod || 'unknown') + '）') + '</span><span>' + Math.round(item.latencyMs) + ' 毫秒</span><span class="og-health-pill ' + (item.status === 'succeeded' ? 'og-health-healthy' : 'og-health-unavailable') + '">' + escapeHtml(displayLabel(item.status)) +
                    '</span><span>' + (item.fallbackUsed ? '是' : '否') + '</span></article>'; }).join('') + '</div>' : emptyState('暂无模型用量', '完成模型路由调用后，用量会显示在这里。'));
    }

    function emptyState(title, message) {
        return '<section class="og-project-empty"><div class="og-empty-icon">◇</div><h2>' + escapeHtml(title) + '</h2><p>' + escapeHtml(message) + '</p></section>';
    }

    function openProviderModal(providerId) {
        var modal = document.getElementById('provider-modal');
        var form = document.getElementById('provider-form');
        var provider = providerId ? state.providers.find(function (item) { return item.id === providerId; }) : null;
        form.reset();
        form.elements.editingId.value = provider ? provider.id : '';
        form.elements.id.value = provider ? provider.id : '';
        form.elements.id.disabled = Boolean(provider);
        form.elements.name.value = provider ? provider.name : '';
        form.elements.providerType.value = provider ? provider.providerType : 'openai-compatible';
        form.elements.providerType.disabled = Boolean(provider);
        form.elements.baseUrl.value = provider ? provider.baseUrl : '';
        form.elements.credentialReference.value = provider ? provider.credentialReference : '';
        form.elements.timeout.value = provider ? provider.timeout : 120;
        document.getElementById('provider-modal-title').textContent = provider ? '编辑服务商' : '添加服务商';
        document.getElementById('provider-form-error').textContent = '';
        modal.hidden = false;
    }

    async function saveProvider(event) {
        event.preventDefault();
        var form = event.currentTarget;
        var editingId = form.elements.editingId.value;
        var payload = {name: form.elements.name.value.trim(), baseUrl: form.elements.baseUrl.value.trim(), credentialReference: form.elements.credentialReference.value.trim(), timeout: Number(form.elements.timeout.value)};
        if (!editingId) {
            payload.id = form.elements.id.value.trim();
            payload.providerType = form.elements.providerType.value;
            payload.enabled = true;
        }
        var submit = form.querySelector('[type="submit"]');
        submit.disabled = true;
        try {
            if (editingId) await api.updateProvider(editingId, payload); else await api.createProvider(payload);
            document.getElementById('provider-modal').hidden = true;
            await loadData();
            render();
        } catch (error) {
            document.getElementById('provider-form-error').textContent = error.message;
        } finally {
            submit.disabled = false;
        }
    }

    async function testProvider(button) {
        var providerId = button.getAttribute('data-test-provider');
        var model = state.models.find(function (item) { return item.providerId === providerId && item.enabled; });
        var resultNode = document.getElementById('connection-' + providerId);
        button.disabled = true;
        resultNode.textContent = '正在测试连接…';
        try {
            var result = await api.testProvider(providerId, model ? model.id : null);
            resultNode.textContent = result.health.message + ' · ' + Math.round(result.health.latencyMs) + ' 毫秒';
            var pill = button.closest('article').querySelector('.og-health-pill');
            pill.className = 'og-health-pill og-health-' + result.health.status;
            pill.textContent = displayLabel(result.health.status);
            await loadData();
        } catch (error) {
            resultNode.textContent = error.message;
        } finally {
            button.disabled = false;
        }
    }

    async function toggleProvider(button) {
        var provider = state.providers.find(function (item) { return item.id === button.getAttribute('data-toggle-provider'); });
        button.disabled = true;
        try {
            await api.updateProvider(provider.id, {enabled: !provider.enabled});
            await loadData();
            render();
        } catch (error) {
            button.disabled = false;
            button.textContent = '失败';
            button.title = error.message;
        }
    }

    async function loadData() {
        var results = await Promise.all([api.capabilities(), api.listProviders(), api.listModels(), api.listRoutingPolicies(), api.listUsage()]);
        state.capabilities = results[0].capabilities || {};
        state.providers = results[1].items || [];
        state.models = results[2].items || [];
        state.policies = results[3].items || [];
        state.usage = results[4].items || [];
        state.usageSummary = results[4].summary || {};
    }

    async function refreshUsage() {
        window.clearTimeout(state.refreshTimer);
        if (state.tab !== 'usage') return;
        try {
            var result = await api.listUsage();
            state.usage = result.items || [];
            state.usageSummary = result.summary || {};
            render();
        } catch (_error) {
            // Keep the last successful snapshot and retry while the tab is visible.
        } finally {
            if (state.tab === 'usage') state.refreshTimer = window.setTimeout(refreshUsage, 2000);
        }
    }

    async function mount() {
        window.clearTimeout(state.refreshTimer);
        state.tab = new URLSearchParams(window.location.search).get('tab') || 'providers';
        root.innerHTML = header() + '<div class="og-workspace-content"><div class="og-loading-state">正在加载模型控制平面…</div></div>';
        try {
            await loadData();
            render();
            refreshUsage();
        } catch (error) {
            root.innerHTML = header() + '<div class="og-workspace-content">' + emptyState('模型控制平面不可用', error.message) + '</div>';
        }
    }

    mount();
})();
