(function () {
    'use strict';

    var api = window.OxyGentApp.api;
    var root = document.getElementById('files-page');
    var artifactLabels = {RequirementSpec: '需求规格', ArchitectureDecision: '架构决策', TaskGraph: '任务图', ReviewReport: '审查报告'};

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function shell(content) {
        root.innerHTML = '<header class="og-workspace-header"><div><p class="og-workspace-eyebrow">共享上下文</p><h1 class="og-workspace-title">文件</h1>' +
            '<p class="og-header-detail">按项目浏览附件引用和结构化产物。这里不是源代码编辑器。</p></div><span class="og-preview-badge">引用</span></header>' +
            '<div class="og-workspace-content">' + content + '</div>';
    }

    async function showProject(projectId) {
        var content = document.getElementById('file-reference-content');
        content.innerHTML = '<div class="og-loading-state">正在加载引用…</div>';
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
            content.innerHTML = '<div class="og-detail-grid"><section class="og-detail-card"><div class="og-card-title"><h2>附件引用</h2><span>' + attachments.length + '</span></div>' +
                (attachments.length ? '<div class="og-record-list">' + attachments.map(function (item) { return '<article><div><b>' + escapeHtml(item.reference) + '</b><p>引用任务：' + escapeHtml(item.task) + '</p></div><span class="og-status-pill">引用</span></article>'; }).join('') + '</div>' : '<p class="og-muted-copy">暂无附件引用。</p>') +
                '</section><section class="og-detail-card"><div class="og-card-title"><h2>产物</h2><span>' + results[1].items.length + '</span></div>' +
                (results[1].items.length ? '<div class="og-record-list">' + results[1].items.map(function (artifact) { return '<article><div><b>' + escapeHtml(artifactLabels[artifact.type] || artifact.type) + '</b><p>' + escapeHtml(artifact.content.summary || '结构化产物') + '</p></div><span>修订 ' + artifact.revision + '</span></article>'; }).join('') + '</div>' : '<p class="og-muted-copy">暂无产物。</p>') + '</section></div>';
        } catch (error) {
            content.innerHTML = '<div class="og-project-empty"><h2>无法获取引用</h2><p>' + escapeHtml(error.message) + '</p></div>';
        }
    }

    async function mount() {
        shell('<div class="og-loading-state">正在加载项目…</div>');
        try {
            var data = await api.listProjects();
            var projects = data.items || [];
            if (!projects.length) {
                shell('<div class="og-project-empty"><h2>暂无项目</h2><p>请先创建项目，再浏览附件和产物引用。</p></div>');
                return;
            }
            shell('<div class="og-reference-filter"><label>项目<select id="file-project-select">' + projects.map(function (project) {
                return '<option value="' + escapeHtml(project.id) + '">' + escapeHtml(project.name) + '</option>';
            }).join('') + '</select></label><span>这里只显示不透明的附件引用，不会复制文件内容。</span></div><div id="file-reference-content"></div>');
            var select = document.getElementById('file-project-select');
            select.addEventListener('change', function () { showProject(select.value); });
            await showProject(select.value);
        } catch (error) {
            shell('<div class="og-project-empty"><h2>项目 API 尚未配置</h2><p>' + escapeHtml(error.message) + '</p></div>');
        }
    }

    mount();
})();
