(function () {
    'use strict';

    var api = window.OxyGentApp.api;
    var root = document.getElementById('files-page');

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function shell(content) {
        root.innerHTML = '<header class="og-workspace-header"><div><p class="og-workspace-eyebrow">Shared context</p><h1 class="og-workspace-title">Files</h1>' +
            '<p class="og-header-detail">Browse attachment references and structured Artifacts by Project. This is not a source-code editor.</p></div><span class="og-preview-badge">References</span></header>' +
            '<div class="og-workspace-content">' + content + '</div>';
    }

    async function showProject(projectId) {
        var content = document.getElementById('file-reference-content');
        content.innerHTML = '<div class="og-loading-state">Loading references…</div>';
        try {
            var results = await Promise.all([api.listTasks(projectId), api.listArtifacts(projectId, false)]);
            var attachments = [];
            results[0].items.forEach(function (task) {
                task.attachmentReferences.forEach(function (reference) {
                    if (!attachments.some(function (item) { return item.reference === reference; })) {
                        attachments.push({reference: reference, task: task.title});
                    }
                });
            });
            content.innerHTML = '<div class="og-detail-grid"><section class="og-detail-card"><div class="og-card-title"><h2>Attachment references</h2><span>' + attachments.length + '</span></div>' +
                (attachments.length ? '<div class="og-record-list">' + attachments.map(function (item) { return '<article><div><b>' + escapeHtml(item.reference) + '</b><p>Referenced by ' + escapeHtml(item.task) + '</p></div><span class="og-status-pill">reference</span></article>'; }).join('') + '</div>' : '<p class="og-muted-copy">No attachment references.</p>') +
                '</section><section class="og-detail-card"><div class="og-card-title"><h2>Artifacts</h2><span>' + results[1].items.length + '</span></div>' +
                (results[1].items.length ? '<div class="og-record-list">' + results[1].items.map(function (artifact) { return '<article><div><b>' + escapeHtml(artifact.type) + '</b><p>' + escapeHtml(artifact.content.summary || 'Structured Artifact') + '</p></div><span>r' + artifact.revision + '</span></article>'; }).join('') + '</div>' : '<p class="og-muted-copy">No Artifacts.</p>') + '</section></div>';
        } catch (error) {
            content.innerHTML = '<div class="og-project-empty"><h2>References unavailable</h2><p>' + escapeHtml(error.message) + '</p></div>';
        }
    }

    async function mount() {
        shell('<div class="og-loading-state">Loading Projects…</div>');
        try {
            var data = await api.listProjects();
            var projects = data.items || [];
            if (!projects.length) {
                shell('<div class="og-project-empty"><h2>No Projects</h2><p>Create a Project before browsing attachment and Artifact references.</p></div>');
                return;
            }
            shell('<div class="og-reference-filter"><label>Project<select id="file-project-select">' + projects.map(function (project) {
                return '<option value="' + escapeHtml(project.id) + '">' + escapeHtml(project.name) + '</option>';
            }).join('') + '</select></label><span>Only opaque attachment references are shown; file content is not copied.</span></div><div id="file-reference-content"></div>');
            var select = document.getElementById('file-project-select');
            select.addEventListener('change', function () { showProject(select.value); });
            await showProject(select.value);
        } catch (error) {
            shell('<div class="og-project-empty"><h2>Projects API is not configured</h2><p>' + escapeHtml(error.message) + '</p></div>');
        }
    }

    mount();
})();
