(function () {
    'use strict';

    var api = window.OxyGentApp.api;
    var root = document.getElementById('projects-page');
    var state = {projects: [], project: null, tasks: [], artifacts: [], activity: [], tab: 'overview'};
    var tabs = ['Overview', 'Ideas', 'Requirements', 'Architecture', 'Tasks', 'Code', 'Artifacts', 'Team', 'Activity', 'Settings'];

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function formatDate(value) {
        if (!value) return 'No activity';
        return new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}).format(new Date(value));
    }

    function formatCost(value) {
        return '$' + Number(value || 0).toFixed(2);
    }

    function header(title, detail) {
        return '<header class="og-workspace-header"><div><p class="og-workspace-eyebrow">Project workspace</p>' +
            '<h1 class="og-workspace-title">' + escapeHtml(title) + '</h1>' +
            (detail ? '<p class="og-header-detail">' + escapeHtml(detail) + '</p>' : '') + '</div>' +
            '<button class="og-primary-button" id="create-project-button">New Project</button></header>';
    }

    function emptyState(title, message) {
        return '<section class="og-project-empty"><div class="og-empty-icon">◇</div><h2>' + escapeHtml(title) +
            '</h2><p>' + escapeHtml(message) + '</p></section>';
    }

    function renderList() {
        root.innerHTML = header('Projects', 'Project-centered requirements, tasks, Artifacts, and collaboration.') +
            '<div class="og-workspace-content"><div class="og-list-toolbar"><div><strong>' + state.projects.length +
            '</strong> projects</div><span>Sorted by latest activity</span></div>' +
            (state.projects.length ? '<div class="og-project-table" role="table"><div class="og-project-row og-project-row-head" role="row">' +
                '<span>Project</span><span>Repository</span><span>Active tasks</span><span>Team</span><span>Last activity</span><span>Monthly cost</span></div>' +
                state.projects.map(function (project) {
                    return '<button class="og-project-row" data-project-id="' + escapeHtml(project.id) + '" role="row">' +
                        '<span class="og-project-name"><b>' + escapeHtml(project.name) + '</b><small>' + escapeHtml(project.description || 'No description') + '</small></span>' +
                        '<span>' + escapeHtml(project.repository || 'Not linked') + '</span><span><b>' + project.activeTasks + '</b></span>' +
                        '<span>' + escapeHtml(project.team.length ? project.team.join(', ') : 'Unassigned') + '</span>' +
                        '<span>' + escapeHtml(formatDate(project.lastActivityAt)) + '</span><span>' + formatCost(project.monthlyCost) + '</span></button>';
                }).join('') + '</div>' : emptyState('Create your first Project', 'Projects keep Chat, role collaboration, tasks, and Artifacts isolated.')) +
            '</div>' + createModal();
        bindSharedActions();
        root.querySelectorAll('[data-project-id]').forEach(function (button) {
            button.addEventListener('click', function () { openProject(button.getAttribute('data-project-id')); });
        });
    }

    function createModal() {
        return '<div class="og-modal-backdrop" id="project-modal" hidden><section class="og-modal" role="dialog" aria-modal="true" aria-labelledby="project-modal-title">' +
            '<div class="og-modal-header"><div><p class="og-workspace-eyebrow">New workspace</p><h2 id="project-modal-title">Create Project</h2></div>' +
            '<button class="og-icon-button" type="button" data-close-modal aria-label="Close">×</button></div>' +
            '<form id="project-create-form"><label>Project name<input name="name" maxlength="160" required placeholder="Platform modernization"></label>' +
            '<label>Description<textarea name="description" maxlength="4000" rows="3" placeholder="What this Project is responsible for"></textarea></label>' +
            '<label>Repository reference<input name="repository" maxlength="500" placeholder="Optional — no repository access is performed"></label>' +
            '<label>Team roles<input name="team" placeholder="Product Manager, Solution Architect"></label>' +
            '<p class="og-form-error" id="project-form-error" role="alert"></p><div class="og-modal-actions"><button type="button" class="og-secondary-button" data-close-modal>Cancel</button>' +
            '<button type="submit" class="og-primary-button">Create Project</button></div></form></section></div>';
    }

    function bindSharedActions() {
        var create = document.getElementById('create-project-button');
        if (create) create.addEventListener('click', function () { document.getElementById('project-modal').hidden = false; });
        root.querySelectorAll('[data-close-modal]').forEach(function (button) {
            button.addEventListener('click', function () { document.getElementById('project-modal').hidden = true; });
        });
        var form = document.getElementById('project-create-form');
        if (form) form.addEventListener('submit', createProject);
    }

    async function createProject(event) {
        event.preventDefault();
        var form = event.currentTarget;
        var submit = form.querySelector('[type="submit"]');
        var error = document.getElementById('project-form-error');
        var data = new FormData(form);
        submit.disabled = true;
        error.textContent = '';
        try {
            var result = await api.createProject({
                name: data.get('name').trim(),
                description: data.get('description').trim(),
                repository: data.get('repository').trim() || null,
                team: data.get('team').split(',').map(function (item) { return item.trim(); }).filter(Boolean),
                settings: {}
            });
            document.getElementById('project-modal').hidden = true;
            await loadProjects();
            await openProject(result.project.id);
        } catch (requestError) {
            error.textContent = requestError.message;
        } finally {
            submit.disabled = false;
        }
    }

    async function openProject(projectId) {
        try {
            var results = await Promise.all([
                api.getProject(projectId), api.listTasks(projectId), api.listArtifacts(projectId, false), api.listActivity(projectId)
            ]);
            state.project = results[0].project;
            state.tasks = results[1].items;
            state.artifacts = results[2].items;
            state.activity = results[3].items;
            var params = new URLSearchParams(window.location.search);
            state.tab = (params.get('tab') || 'overview').toLowerCase();
            history.replaceState(null, '', '?project=' + encodeURIComponent(projectId) + '&tab=' + encodeURIComponent(state.tab));
            renderDetail();
        } catch (error) {
            renderError(error);
        }
    }

    function renderDetail() {
        var project = state.project;
        root.innerHTML = header(project.name, project.description) + '<div class="og-workspace-content">' +
            '<button class="og-back-button" id="back-to-projects">← All Projects</button><nav class="og-section-tabs" aria-label="Project sections">' +
            tabs.map(function (tab) { var id = tab.toLowerCase(); return '<button class="og-section-tab' + (state.tab === id ? ' active' : '') + '" data-project-tab="' + id + '">' + tab + '</button>'; }).join('') +
            '</nav><div id="project-tab-content">' + renderTab() + '</div></div>' + createModal();
        bindSharedActions();
        document.getElementById('back-to-projects').addEventListener('click', function () {
            state.project = null; history.replaceState(null, '', window.location.pathname); renderList();
        });
        root.querySelectorAll('[data-project-tab]').forEach(function (button) {
            button.addEventListener('click', function () {
                state.tab = button.getAttribute('data-project-tab');
                history.replaceState(null, '', '?project=' + encodeURIComponent(project.id) + '&tab=' + encodeURIComponent(state.tab));
                renderDetail();
            });
        });
    }

    function renderTab() {
        if (state.tab === 'overview') return renderOverview();
        if (state.tab === 'tasks') return renderTasks();
        if (state.tab === 'artifacts') return renderArtifacts(state.artifacts, 'All Artifacts');
        if (state.tab === 'requirements') return renderArtifacts(state.artifacts.filter(function (item) { return item.type === 'RequirementSpec'; }), 'Requirements');
        if (state.tab === 'architecture') return renderArtifacts(state.artifacts.filter(function (item) { return item.type === 'ArchitectureDecision'; }), 'Architecture decisions');
        if (state.tab === 'team') return renderTeam();
        if (state.tab === 'activity') return renderActivity();
        if (state.tab === 'settings') return '<section class="og-detail-card"><h2>Project settings</h2><pre class="og-json-preview">' + escapeHtml(JSON.stringify(state.project.settings, null, 2)) + '</pre></section>';
        if (state.tab === 'code') return emptyState('Code Workspace is isolated', 'Repository worktrees and code execution arrive in PR 5.');
        return emptyState('No ' + state.tab + ' yet', 'This Project section is ready for structured records in a later focused PR.');
    }

    function renderOverview() {
        var project = state.project;
        return '<div class="og-metric-grid"><article><span>Active tasks</span><strong>' + project.activeTasks + '</strong></article>' +
            '<article><span>Artifacts</span><strong>' + state.artifacts.length + '</strong></article><article><span>Team roles</span><strong>' + project.team.length + '</strong></article>' +
            '<article><span>Monthly cost</span><strong>' + formatCost(project.monthlyCost) + '</strong></article></div>' +
            '<div class="og-detail-grid"><section class="og-detail-card"><h2>Project context</h2><dl><dt>Repository</dt><dd>' + escapeHtml(project.repository || 'Not linked') +
            '</dd><dt>Status</dt><dd><span class="og-status-pill">' + escapeHtml(project.status) + '</span></dd><dt>Last activity</dt><dd>' + escapeHtml(formatDate(project.lastActivityAt)) +
            '</dd></dl></section><section class="og-detail-card"><h2>Latest activity</h2>' + activityItems(state.activity.slice(0, 4)) + '</section></div>';
    }

    function renderTasks() {
        if (!state.tasks.length) return emptyState('No Project Tasks', 'Use Convert to Project Task in Chat to create the first trace-linked task.');
        return '<section class="og-detail-card"><div class="og-card-title"><h2>Project Tasks</h2><span>' + state.tasks.length + ' total</span></div><div class="og-record-list">' +
            state.tasks.map(function (task) { return '<article><div><b>' + escapeHtml(task.title) + '</b><p>' + escapeHtml(task.objective) + '</p></div>' +
                '<div class="og-record-meta"><span class="og-status-pill">' + escapeHtml(task.status) + '</span><span>' + escapeHtml(task.risk) + ' risk</span>' +
                (task.sourceTraceId ? '<code>trace ' + escapeHtml(task.sourceTraceId.slice(0, 12)) + '</code>' : '<span>No trace</span>') + '</div></article>'; }).join('') + '</div></section>';
    }

    function renderArtifacts(items, title) {
        if (!items.length) {
            var emptyTitle = title === 'All Artifacts' ? 'No Artifacts' : 'No ' + title;
            return emptyState(emptyTitle, 'Run the structured role workflow or attach validated Artifacts to this Project.');
        }
        return '<section class="og-detail-card"><div class="og-card-title"><h2>' + escapeHtml(title) + '</h2><span>Append-only revisions</span></div><div class="og-artifact-grid">' +
            items.map(function (artifact) { var summary = artifact.content && artifact.content.summary ? artifact.content.summary : 'Structured Artifact'; return '<article><div class="og-artifact-type">' +
                escapeHtml(artifact.type) + '</div><h3>' + escapeHtml(summary) + '</h3><p>Revision ' + artifact.revision + ' · ' + escapeHtml(artifact.validationStatus) + '</p>' +
                '<div class="og-artifact-provenance"><span>' + escapeHtml(artifact.producerRole) + '</span><span>' + escapeHtml(artifact.providerId) + ' / ' + escapeHtml(artifact.modelId) + '</span></div></article>'; }).join('') + '</div></section>';
    }

    function renderTeam() {
        if (!state.project.team.length) return emptyState('No team assigned', 'Agent Profiles and model assignments arrive in PR 3.');
        return '<section class="og-detail-card"><h2>Assigned roles</h2><div class="og-team-list">' + state.project.team.map(function (role) {
            return '<article><span class="og-role-mark">' + escapeHtml(role.charAt(0).toUpperCase()) + '</span><div><b>' + escapeHtml(role) + '</b><p>Model policy mapping becomes visible in PR 3.</p></div></article>';
        }).join('') + '</div></section>';
    }

    function activityItems(items) {
        if (!items.length) return '<p class="og-muted-copy">No activity recorded.</p>';
        return '<div class="og-activity-list">' + items.map(function (item) { return '<article><span></span><div><b>' + escapeHtml(item.summary) + '</b><small>' + escapeHtml(formatDate(item.createdAt)) + '</small></div></article>'; }).join('') + '</div>';
    }

    function renderActivity() {
        return '<section class="og-detail-card"><h2>Activity</h2>' + activityItems(state.activity) + '</section>';
    }

    function renderError(error) {
        root.innerHTML = header('Projects') + '<div class="og-workspace-content">' + emptyState('Projects API is not configured', error.message + '. Start OxyGent with build_platform_router(PlatformServices()).') + '</div>';
        var button = document.getElementById('create-project-button');
        if (button) button.disabled = true;
    }

    async function loadProjects() {
        var result = await api.listProjects();
        state.projects = result.items || [];
    }

    async function mount() {
        root.innerHTML = header('Projects') + '<div class="og-workspace-content"><div class="og-loading-state">Loading Projects…</div></div>';
        try {
            await loadProjects();
            var selected = new URLSearchParams(window.location.search).get('project');
            if (selected) await openProject(selected); else renderList();
        } catch (error) {
            renderError(error);
        }
    }

    mount();
})();
