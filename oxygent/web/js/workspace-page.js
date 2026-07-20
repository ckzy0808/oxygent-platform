(function () {
    'use strict';

    var pages = {
        projects: {
            eyebrow: 'Project workspace',
            title: 'Projects',
            description: 'Organize ideas, requirements, architecture, tasks, Artifacts, and Agent teams without changing the existing Chat workflow.',
            tabs: ['Overview', 'Ideas', 'Requirements', 'Architecture', 'Tasks', 'Artifacts', 'Team', 'Activity', 'Settings'],
            next: ['Project list and detail APIs', 'Artifact provenance and revisions', 'Convert Chat to Project Task']
        },
        files: {
            eyebrow: 'Shared context',
            title: 'Files',
            description: 'A future home for Chat attachments and Project file references. Source repositories remain isolated in Code Workspace.',
            tabs: ['Recent', 'Attachments', 'Project files', 'Artifacts'],
            next: ['Attachment references', 'Project isolation', 'Safe preview and download']
        },
        agents: {
            eyebrow: 'Agent registry',
            title: 'Agents',
            description: 'Inspect role assignments, model policies, capabilities, Tool policies, runtime status, usage, cost, and success rate.',
            tabs: ['Team', 'Roles', 'Agent profiles', 'Tool policies'],
            next: ['Role-to-model mapping', 'Runtime status', 'Usage and success metrics']
        },
        models: {
            eyebrow: 'Model control plane',
            title: 'Models',
            description: 'Manage Providers, model profiles, routing policies, health, and usage while keeping credentials masked.',
            tabs: ['Providers', 'Models', 'Routing Policies', 'Usage'],
            next: ['Provider health checks', 'Primary and fallback chains', 'Credential references only']
        },
        workflows: {
            eyebrow: 'Structured collaboration',
            title: 'Workflows',
            description: 'Track role-driven work as engineering phases and Artifacts instead of presenting execution as a group chat.',
            tabs: ['Definitions', 'Runs', 'Timeline', 'Artifacts'],
            next: ['Versioned workflow events', 'Phase status projection', 'Advanced Execution Drawer']
        },
        insights: {
            eyebrow: 'Operations and economics',
            title: 'Insights',
            description: 'Understand tokens, estimated cost, latency, success rate, fallback behavior, and task outcomes by Project and role.',
            tabs: ['Overview', 'Usage', 'Cost', 'Reliability'],
            next: ['Project-level aggregation', 'Role and model breakdown', 'Budget warning states']
        },
        settings: {
            eyebrow: 'Platform configuration',
            title: 'Settings',
            description: 'Configure platform defaults, security boundaries, workspace roots, and feature availability without exposing secrets.',
            tabs: ['General', 'Security', 'Workspace', 'Features'],
            next: ['Capability detection', 'Repository allow-list', 'Local and production safeguards']
        },
        code: {
            eyebrow: 'Engineering workspace',
            title: 'Code',
            description: 'Repository context, engineering phases, changes, and verification will live here. PR 1 adds the safe workspace shell only.',
            tabs: ['Repositories', 'Code Tasks', 'Changes', 'Reviews', 'Verification'],
            next: ['Isolated Git worktrees', 'System-enforced Change Contracts', 'Fixed-argument verification commands'],
            code: true
        }
    };

    function escapeHtml(value) {
        return String(value).replace(/[&<>'"]/g, function (character) {
            return ({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                "'": '&#39;',
                '"': '&quot;'
            })[character];
        });
    }

    function codeFoundation() {
        var columns = [
            ['Repository Context', 6],
            ['Task Timeline', 7],
            ['Changes and Verification', 5]
        ];
        return '<section class="og-foundation-card og-code-foundation">' +
            columns.map(function (column, index) {
                var placeholders = '';
                for (var i = 0; i < column[1]; i += 1) {
                    placeholders += '<div class="og-code-placeholder' +
                        (i === index + 1 ? ' emphasis' : '') + '"></div>';
                }
                return '<div class="og-code-column"><p class="og-code-column-label">' +
                    escapeHtml(column[0]) + '</p>' + placeholders + '</div>';
            }).join('') +
        '</section>';
    }

    function mount() {
        var root = document.getElementById('workspace-page');
        if (!root) return;
        var pageId = document.body.getAttribute('data-oxygent-page');
        var page = pages[pageId];
        if (!page) return;
        document.title = page.title + ' - OxyGent';
        root.innerHTML =
            '<header class="og-workspace-header">' +
                '<div><p class="og-workspace-eyebrow">' + escapeHtml(page.eyebrow) + '</p>' +
                '<h1 class="og-workspace-title">' + escapeHtml(page.title) + '</h1></div>' +
                '<span class="og-preview-badge">Foundation</span>' +
            '</header>' +
            '<div class="og-workspace-content">' +
                '<nav class="og-section-tabs" aria-label="' + escapeHtml(page.title) + ' sections">' +
                    page.tabs.map(function (tab, index) {
                        return '<span class="og-section-tab' + (index === 0 ? ' active' : '') + '">' +
                            escapeHtml(tab) + '</span>';
                    }).join('') +
                '</nav>' +
                '<div class="og-foundation-grid">' +
                    '<section class="og-foundation-card"><h2>Workspace foundation is ready</h2>' +
                    '<p>' + escapeHtml(page.description) + '</p>' +
                    '<div class="og-empty-visual" aria-hidden="true"><div class="og-empty-block"></div>' +
                    '<div class="og-empty-block"></div><div class="og-empty-block"></div></div></section>' +
                    '<aside class="og-foundation-card"><h2>Coming in its focused PR</h2><ul class="og-foundation-list">' +
                    page.next.map(function (item) { return '<li>' + escapeHtml(item) + '</li>'; }).join('') +
                    '</ul></aside>' +
                    (page.code ? codeFoundation() : '') +
                '</div>' +
            '</div>';
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
    } else {
        mount();
    }
})();
