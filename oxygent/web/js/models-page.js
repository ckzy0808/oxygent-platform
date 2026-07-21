(function () {
    'use strict';

    var api = window.OxyGentApp.api;
    var root = document.getElementById('models-page');
    var state = {tab: 'providers', capabilities: {}, providers: [], models: [], policies: [], usage: [], usageSummary: {}};
    var tabs = ['Providers', 'Models', 'Routing Policies', 'Usage'];

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function formatCost(value) {
        return '$' + Number(value || 0).toFixed(4);
    }

    function formatNumber(value) {
        return Number(value || 0).toLocaleString();
    }

    function header() {
        var addDisabled = state.capabilities.providerMutations ? '' : ' disabled title="Provider mutations are disabled by server policy"';
        return '<header class="og-workspace-header"><div><p class="og-workspace-eyebrow">Model control plane</p><h1 class="og-workspace-title">Models</h1>' +
            '<p class="og-header-detail">Providers, models, routing policies, health, and usage without exposing resolved credentials.</p></div>' +
            '<button class="og-primary-button" id="add-provider-button"' + addDisabled + '>Add Provider</button></header>';
    }

    function shell(content) {
        root.innerHTML = header() + '<div class="og-workspace-content"><nav class="og-section-tabs" aria-label="Model sections">' + tabs.map(function (tab) {
            var id = tab.toLowerCase().replace(' ', '-');
            return '<button class="og-section-tab' + (state.tab === id ? ' active' : '') + '" data-model-tab="' + id + '">' + tab + '</button>';
        }).join('') + '</nav><div id="model-tab-content">' + content + '</div></div>' + providerModal();
        bindNavigation();
    }

    function providerModal() {
        return '<div class="og-modal-backdrop" id="provider-modal" hidden><section class="og-modal" role="dialog" aria-modal="true" aria-labelledby="provider-modal-title">' +
            '<div class="og-modal-header"><div><p class="og-workspace-eyebrow">Credential-safe configuration</p><h2 id="provider-modal-title">Add Provider</h2></div>' +
            '<button type="button" class="og-icon-button" data-close-provider aria-label="Close">×</button></div><form id="provider-form">' +
            '<input type="hidden" name="editingId"><label>Provider ID<input name="id" required maxlength="120" pattern="[a-zA-Z0-9_.-]+" placeholder="provider-id"></label>' +
            '<label>Name<input name="name" required maxlength="160" placeholder="Provider display name"></label><label>Protocol<select name="providerType">' +
            '<option value="openai-compatible">OpenAI-compatible</option><option value="gemini">Gemini</option><option value="ollama">Ollama</option></select></label>' +
            '<label>Base URL<input name="baseUrl" type="url" required maxlength="1000" placeholder="https://provider.example/v1"></label>' +
            '<label>Credential reference<input name="credentialReference" maxlength="300" placeholder="env:PROVIDER_API_KEY"><small>Enter a secret reference, never an API key value.</small></label>' +
            '<label>Timeout (seconds)<input name="timeout" type="number" min="1" max="3600" value="120" required></label>' +
            '<p class="og-form-error" id="provider-form-error" role="alert"></p><div class="og-modal-actions"><button type="button" class="og-secondary-button" data-close-provider>Cancel</button>' +
            '<button type="submit" class="og-primary-button">Save Provider</button></div></form></section></div>';
    }

    function bindNavigation() {
        root.querySelectorAll('[data-model-tab]').forEach(function (button) {
            button.addEventListener('click', function () {
                state.tab = button.getAttribute('data-model-tab');
                history.replaceState(null, '', '?tab=' + encodeURIComponent(state.tab));
                render();
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
        if (!state.providers.length) return emptyState('No Providers configured', 'Add a credential reference and Provider protocol to begin.');
        return '<div class="og-control-toolbar"><div><strong>' + state.providers.length + '</strong> Providers</div><span>' +
            (state.capabilities.providerMutations ? 'Local Provider administration enabled' : 'Provider administration is read-only') + '</span></div>' +
            '<div class="og-provider-grid">' + state.providers.map(function (provider) {
                var providerModels = state.models.filter(function (model) { return model.providerId === provider.id; });
                var disabled = state.capabilities.providerMutations ? '' : ' disabled';
                var testDisabled = !state.capabilities.providerMutations || !providerModels.length ? ' disabled' : '';
                return '<article><div class="og-provider-card-head"><div class="og-provider-logo">' + escapeHtml(provider.name.charAt(0).toUpperCase()) + '</div><div><h2>' + escapeHtml(provider.name) + '</h2>' +
                    '<span>' + escapeHtml(provider.providerType) + '</span></div><span class="og-health-pill og-health-' + provider.healthStatus + '">' + escapeHtml(provider.healthStatus) + '</span></div>' +
                    '<dl><dt>Base URL</dt><dd title="' + escapeHtml(provider.baseUrl) + '">' + escapeHtml(provider.baseUrl) + '</dd><dt>Credential</dt><dd>' + escapeHtml(provider.credentialMask) + '</dd>' +
                    '<dt>Reference</dt><dd><code>' + escapeHtml(provider.credentialReference || 'Not configured') + '</code></dd><dt>Models</dt><dd>' + providerModels.length + '</dd></dl>' +
                    '<div class="og-provider-actions"><button class="og-secondary-button" data-edit-provider="' + escapeHtml(provider.id) + '"' + disabled + '>Edit</button>' +
                    '<button class="og-secondary-button" data-test-provider="' + escapeHtml(provider.id) + '"' + testDisabled + '>Test Connection</button>' +
                    '<button class="og-text-button" data-toggle-provider="' + escapeHtml(provider.id) + '"' + disabled + '>' + (provider.enabled ? 'Disable' : 'Enable') + '</button></div>' +
                    '<p class="og-connection-result" id="connection-' + escapeHtml(provider.id) + '"></p></article>';
            }).join('') + '</div>';
    }

    function renderModels() {
        if (!state.models.length) return emptyState('No Models registered', 'Register ModelProfile records in the PlatformControlPlane.');
        return '<div class="og-control-table"><div class="og-model-row og-control-head"><span>Model</span><span>Provider</span><span>Capabilities</span><span>Context</span><span>Latency</span><span>Cost</span><span>Health</span><span>Assigned roles</span></div>' +
            state.models.map(function (model) { return '<article class="og-model-row"><div><b>' + escapeHtml(model.displayName) + '</b><small>' + escapeHtml(model.modelName) + '</small></div><span>' + escapeHtml(model.providerName) + '</span>' +
                '<div class="og-chip-list">' + model.capabilities.map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join('') + '</div><span>' + formatNumber(model.contextWindow) + '</span>' +
                '<span>Tier ' + model.latencyTier + '</span><span>Tier ' + model.costTier + '</span><span class="og-health-pill og-health-' + model.healthStatus + '">' + escapeHtml(model.healthStatus) + '</span>' +
                '<span>' + escapeHtml(model.assignedRoles.join(', ') || 'Unassigned') + '</span></article>'; }).join('') + '</div>';
    }

    function renderPolicies() {
        if (!state.policies.length) return emptyState('No Routing Policies', 'Create one role policy for every Agent Profile.');
        return '<div class="og-policy-grid">' + state.policies.map(function (policy) { return '<article><div class="og-policy-head"><div><span>Role</span><h2>' + escapeHtml(policy.role.name) + '</h2></div><span class="og-routing-pill og-routing-' +
            (policy.routingMode === 'priority' && policy.primaryModels.length === 1 ? 'fixed' : 'auto') + '">' + escapeHtml(policy.routingMode) + '</span></div>' +
            '<div class="og-policy-chain"><div><b>Primary models</b>' + policy.primaryModels.map(modelBadge).join('') + '</div><div><b>Fallback models</b>' +
            (policy.fallbackModels.length ? policy.fallbackModels.map(modelBadge).join('') : '<span class="og-muted-copy">None</span>') + '</div></div>' +
            '<dl><dt>Required capabilities</dt><dd>' + escapeHtml(policy.requiredCapabilities.join(', ') || 'None') + '</dd><dt>Excluded Providers</dt><dd>' + escapeHtml(policy.excludedProviders.join(', ') || 'None') +
            '</dd><dt>Budget</dt><dd>' + (policy.maxCostPerRun == null ? 'Not capped' : formatCost(policy.maxCostPerRun)) + '</dd><dt>Reviewer independence</dt><dd>' + (policy.excludeSameProviderAsProducer ? 'Required' : 'Not required') + '</dd></dl></article>'; }).join('') + '</div>';
    }

    function modelBadge(model) {
        return '<span class="og-model-badge"><b>' + escapeHtml(model.displayName) + '</b><small>' + escapeHtml(model.providerName) + '</small></span>';
    }

    function renderUsage() {
        var summary = state.usageSummary;
        return '<div class="og-metric-grid"><article><span>Invocations</span><strong>' + formatNumber(summary.invocations) + '</strong></article><article><span>Input tokens</span><strong>' + formatNumber(summary.inputTokens) +
            '</strong></article><article><span>Output tokens</span><strong>' + formatNumber(summary.outputTokens) + '</strong></article><article><span>Estimated cost</span><strong>' + formatCost(summary.estimatedCost) + '</strong></article></div>' +
            (state.usage.length ? '<div class="og-control-table"><div class="og-usage-row og-control-head"><span>Role</span><span>Provider / Model</span><span>Tokens</span><span>Latency</span><span>Cost</span><span>Status</span><span>Fallback</span></div>' +
                state.usage.map(function (item) { var model = state.models.find(function (entry) { return entry.id === item.modelId; }); var provider = state.providers.find(function (entry) { return entry.id === item.providerId; }); return '<article class="og-usage-row"><span>' + escapeHtml(item.roleId) + '</span><div><b>' + escapeHtml(provider ? provider.name : item.providerId) + '</b><small>' + escapeHtml(model ? model.displayName : item.modelId) +
                    '</small></div><span>' + formatNumber(item.inputTokens + item.outputTokens) + '</span><span>' + Math.round(item.latencyMs) + ' ms</span><span>' + (item.costAvailable === false ? 'Unavailable' : formatCost(item.estimatedCost)) + '</span><span class="og-health-pill ' + (item.status === 'succeeded' ? 'og-health-healthy' : 'og-health-unavailable') + '">' + escapeHtml(item.status) +
                    '</span><span>' + (item.fallbackUsed ? 'Yes' : 'No') + '</span></article>'; }).join('') + '</div>' : emptyState('No Model Usage', 'Usage appears after routed model calls.'));
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
        document.getElementById('provider-modal-title').textContent = provider ? 'Edit Provider' : 'Add Provider';
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
        resultNode.textContent = 'Testing connection…';
        try {
            var result = await api.testProvider(providerId, model ? model.id : null);
            resultNode.textContent = result.health.message + ' · ' + Math.round(result.health.latencyMs) + ' ms';
            var pill = button.closest('article').querySelector('.og-health-pill');
            pill.className = 'og-health-pill og-health-' + result.health.status;
            pill.textContent = result.health.status;
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
            button.textContent = 'Failed';
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

    async function mount() {
        state.tab = new URLSearchParams(window.location.search).get('tab') || 'providers';
        root.innerHTML = header() + '<div class="og-workspace-content"><div class="og-loading-state">Loading Model control plane…</div></div>';
        try {
            await loadData();
            render();
        } catch (error) {
            root.innerHTML = header() + '<div class="og-workspace-content">' + emptyState('Model control plane unavailable', error.message) + '</div>';
        }
    }

    mount();
})();
