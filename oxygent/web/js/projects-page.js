(function () {
    'use strict';

    var api = window.OxyGentApp.api;
    var root = document.getElementById('projects-page');
    var state = {projects: [], project: null, tasks: [], artifacts: [], activity: [], workflowRuns: [], sources: [], sourceAnalyses: [], capabilities: {}, tab: 'overview', selectedArtifactId: '', notice: ''};
    var tabs = [
        ['overview', '概览'], ['ideas', '创意'], ['requirements', '需求'], ['architecture', '架构'],
        ['tasks', '任务'], ['code', '代码'], ['artifacts', '产物'], ['team', '团队'], ['activity', '活动'], ['settings', '设置']
    ];
    var roleLabels = {product_manager: '产品经理', solution_architect: '解决方案架构师', technical_lead: '技术负责人', reviewer: '审查员', 'Product Manager': '产品经理', 'Solution Architect': '解决方案架构师', 'Technical Lead': '技术负责人', 'Reviewer': '审查员'};
    var statusLabels = {active: '进行中', draft: '草稿', archived: '已归档', ready: '就绪', inProgress: '进行中', analyzing: '分析中', planning: '规划中', reviewing: '审查中', 'awaiting-implementation': '等待实现', blocked: '已阻塞', failed: '失败', completed: '已完成', valid: '有效', invalid: '无效', unvalidated: '未验证'};
    var riskLabels = {low: '低风险', medium: '中风险', high: '高风险'};
    var artifactLabels = {RequirementSpec: '需求规格', ArchitectureDecision: '架构决策', TaskGraph: '任务图', ReviewReport: '审查报告'};

    function label(value, labels) {
        return labels[value] || value;
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function formatDate(value) {
        if (!value) return '暂无活动';
        return new Intl.DateTimeFormat('zh-CN', {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}).format(new Date(value));
    }

    async function listSourceAnalysesSafe(projectId) {
        try {
            return await api.listSourceAnalyses(projectId);
        } catch (_error) {
            return {items: []};
        }
    }

    function projectUrl() {
        var params = new URLSearchParams();
        params.set('project', state.project.id);
        params.set('tab', state.tab);
        if (state.selectedArtifactId) params.set('artifact', state.selectedArtifactId);
        return '?' + params.toString();
    }

    function header(title, detail) {
        return '<header class="og-workspace-header"><div><p class="og-workspace-eyebrow">项目工作区</p>' +
            '<h1 class="og-workspace-title">' + escapeHtml(title) + '</h1>' +
            (detail ? '<p class="og-header-detail">' + escapeHtml(detail) + '</p>' : '') + '</div>' +
            '<button class="og-primary-button" id="create-project-button">新建项目</button></header>';
    }

    function emptyState(title, message) {
        return '<section class="og-project-empty"><div class="og-empty-icon">◇</div><h2>' + escapeHtml(title) +
            '</h2><p>' + escapeHtml(message) + '</p></section>';
    }

    function renderList() {
        root.innerHTML = header('项目', '以项目为中心管理需求、任务、产物和团队协作。') +
            '<div class="og-workspace-content"><div class="og-list-toolbar"><div><strong>' + state.projects.length +
            '</strong> 个项目</div><span>按最近活动时间排序</span></div>' +
            (state.projects.length ? '<div class="og-project-table" role="table"><div class="og-project-row og-project-row-head" role="row">' +
                '<span>项目</span><span>代码仓库</span><span>活动任务</span><span>团队</span><span>最近活动</span></div>' +
                state.projects.map(function (project) {
                    return '<button class="og-project-row" data-project-id="' + escapeHtml(project.id) + '" role="row">' +
                        '<span class="og-project-name"><b>' + escapeHtml(project.name) + '</b><small>' + escapeHtml(project.description || '暂无描述') + '</small></span>' +
                        '<span>' + escapeHtml(project.repository || '未关联') + '</span><span><b>' + project.activeTasks + '</b></span>' +
                        '<span>' + escapeHtml(project.team.length ? project.team.map(function (role) { return label(role, roleLabels); }).join('、') : '未分配') + '</span>' +
                        '<span>' + escapeHtml(formatDate(project.lastActivityAt)) + '</span></button>';
                }).join('') + '</div>' : emptyState('创建第一个项目', '项目可以隔离管理对话、角色协作、任务和产物。')) +
            '</div>' + createModal();
        bindSharedActions();
        root.querySelectorAll('[data-project-id]').forEach(function (button) {
            button.addEventListener('click', function () { openProject(button.getAttribute('data-project-id')); });
        });
    }

    function createModal() {
        return '<div class="og-modal-backdrop" id="project-modal" hidden><section class="og-modal" role="dialog" aria-modal="true" aria-labelledby="project-modal-title">' +
            '<div class="og-modal-header"><div><p class="og-workspace-eyebrow">新工作区</p><h2 id="project-modal-title">创建项目</h2></div>' +
            '<button class="og-icon-button" type="button" data-close-modal aria-label="关闭">×</button></div>' +
            '<form id="project-create-form"><input type="hidden" name="creationMode" value="idea"><div class="og-project-create-modes">' +
            '<button type="button" class="active" data-project-mode="idea"><b>从想法创建</b><span>先描述产品想法，再运行四角色规划流程</span></button>' +
            '<button type="button" data-project-mode="import"><b>导入现有项目</b><span>先分析现有代码，再提出改进需求</span></button></div>' +
            '<label>项目名称<input name="name" maxlength="160" required placeholder="例如：平台现代化改造"></label>' +
            '<label data-idea-field>项目描述<textarea name="description" maxlength="4000" rows="3" placeholder="请描述该项目负责的范围"></textarea></label>' +
            '<section class="og-project-import-panel" data-import-panel hidden><input id="project-import-folder" type="file" webkitdirectory directory multiple hidden>' +
            '<button type="button" class="og-secondary-button" data-select-project-folder>选择项目文件夹</button><div><b id="project-import-name">尚未选择文件夹</b>' +
            '<p>自动跳过依赖、虚拟环境、构建产物和凭证文件；上传的是受管副本，不修改原文件。</p></div></section>' +
            '<label>团队角色<input name="team" placeholder="产品经理、解决方案架构师"></label>' +
            '<p class="og-form-error" id="project-form-error" role="alert"></p><div class="og-modal-actions"><button type="button" class="og-secondary-button" data-close-modal>取消</button>' +
            '<button type="submit" class="og-primary-button">创建项目</button></div></form></section></div>';
    }

    function bindSharedActions() {
        var create = document.getElementById('create-project-button');
        if (create) create.addEventListener('click', function () { document.getElementById('project-modal').hidden = false; });
        root.querySelectorAll('[data-close-modal]').forEach(function (button) {
            button.addEventListener('click', function () { document.getElementById('project-modal').hidden = true; });
        });
        var form = document.getElementById('project-create-form');
        if (form) form.addEventListener('submit', createProject);
        root.querySelectorAll('[data-project-mode]').forEach(function (button) {
            button.addEventListener('click', function () { selectProjectMode(button.getAttribute('data-project-mode')); });
        });
        var folderButton = root.querySelector('[data-select-project-folder]');
        var folderInput = document.getElementById('project-import-folder');
        if (folderButton && folderInput) folderButton.addEventListener('click', function () { folderInput.click(); });
        if (folderInput) folderInput.addEventListener('change', projectFolderSelected);
    }

    function selectProjectMode(mode) {
        var form = document.getElementById('project-create-form');
        if (!form) return;
        form.elements.creationMode.value = mode;
        form.querySelector('[data-import-panel]').hidden = mode !== 'import';
        form.querySelector('[data-idea-field]').hidden = mode === 'import';
        form.querySelectorAll('[data-project-mode]').forEach(function (button) {
            button.classList.toggle('active', button.getAttribute('data-project-mode') === mode);
        });
        form.querySelector('[type="submit"]').textContent = mode === 'import' ? '导入并分析项目' : '创建项目';
    }

    function projectFolderSelected(event) {
        var files = event.target.files;
        if (!files || !files.length) return;
        var path = files[0].webkitRelativePath || files[0].name;
        var folderName = path.split('/')[0] || '上传的项目';
        document.getElementById('project-import-name').textContent = folderName + ' · 浏览器发现 ' + files.length + ' 个文件（上传后按源码规则精确统计）';
        var nameInput = document.querySelector('#project-create-form [name="name"]');
        if (nameInput && !nameInput.value.trim()) nameInput.value = folderName;
    }

    async function createProject(event) {
        event.preventDefault();
        var form = event.currentTarget;
        var submit = form.querySelector('[type="submit"]');
        var error = document.getElementById('project-form-error');
        var data = new FormData(form);
        var mode = data.get('creationMode');
        var folderInput = document.getElementById('project-import-folder');
        if (mode === 'import' && (!folderInput.files || !folderInput.files.length)) {
            error.textContent = '请先选择要改进的项目文件夹。';
            return;
        }
        submit.disabled = true;
        submit.textContent = mode === 'import' ? '正在创建项目…' : '正在创建…';
        error.textContent = '';
        var createdProjectId = '';
        try {
            var result = await api.createProject({
                name: data.get('name').trim(),
                description: data.get('description').trim(),
                repository: mode === 'import' ? (folderInput.files[0].webkitRelativePath || folderInput.files[0].name).split('/')[0] : null,
                team: data.get('team').split(/[,，、]/).map(function (item) { return item.trim(); }).filter(Boolean),
                settings: {creationMode: mode}
            });
            createdProjectId = result.project.id;
            if (mode === 'import') {
                submit.textContent = '正在上传源码…';
                var sourceResult = await api.importSourceWorkspace(result.project.id, folderInput.files, result.project.repository);
                submit.textContent = '智能体正在理解项目…';
                if (state.capabilities.projectSourceAnalysis) {
                    try {
                        var analysisResult = await api.analyzeSourceWorkspace(result.project.id, sourceResult.sourceWorkspace.id);
                        if (!result.project.description) await api.updateProject(result.project.id, {description: analysisResult.analysis.summary});
                        state.notice = '已导入 ' + sourceResult.sourceWorkspace.fileCount + ' 个源码文件并完成智能体分析。现在可以提出改进需求。';
                    } catch (analysisError) {
                        state.notice = '项目源码已导入，但自动分析失败：' + analysisError.message + '。你仍可在代码页面使用该项目。';
                    }
                } else {
                    state.notice = '项目源码已导入。重启到新版后端后即可让智能体分析项目并进入改进流程。';
                }
            } else {
                state.notice = '';
            }
            document.getElementById('project-modal').hidden = true;
            await loadProjects();
            await openProject(result.project.id);
        } catch (requestError) {
            error.textContent = requestError.message;
            if (createdProjectId) {
                try { await api.deleteProject(createdProjectId); } catch (_cleanupError) { /* Preserve server error above. */ }
            }
        } finally {
            submit.disabled = false;
            submit.textContent = mode === 'import' ? '导入并分析项目' : '创建项目';
        }
    }

    async function openProject(projectId) {
        try {
            var results = await Promise.all([
                api.getProject(projectId), api.listTasks(projectId), api.listArtifacts(projectId, false), api.listActivity(projectId),
                api.listWorkflowRuns({projectId: projectId}), api.capabilities(), api.listSourceWorkspaces(projectId), listSourceAnalysesSafe(projectId)
            ]);
            state.project = results[0].project;
            state.tasks = results[1].items;
            state.artifacts = results[2].items;
            state.activity = results[3].items;
            state.workflowRuns = results[4].items || [];
            state.capabilities = results[5].capabilities || {};
            state.sources = results[6].items || [];
            state.sourceAnalyses = results[7].items || [];
            var params = new URLSearchParams(window.location.search);
            state.tab = (params.get('tab') || 'overview').toLowerCase();
            state.selectedArtifactId = params.get('artifact') || '';
            if (state.selectedArtifactId) state.tab = 'artifacts';
            history.replaceState(null, '', projectUrl());
            renderDetail();
        } catch (error) {
            renderError(error);
        }
    }

    function renderDetail() {
        var project = state.project;
        root.innerHTML = header(project.name, project.description) + '<div class="og-workspace-content">' +
            (state.notice ? '<div class="og-project-notice">' + escapeHtml(state.notice) + '</div>' : '') +
            '<button class="og-back-button" id="back-to-projects">← 全部项目</button><nav class="og-section-tabs" aria-label="项目栏目">' +
            tabs.map(function (tab) { return '<button class="og-section-tab' + (state.tab === tab[0] ? ' active' : '') + '" data-project-tab="' + tab[0] + '">' + tab[1] + '</button>'; }).join('') +
            '</nav><div id="project-tab-content">' + renderTab() + '</div></div>' + createModal();
        bindSharedActions();
        document.getElementById('back-to-projects').addEventListener('click', function () {
            state.project = null; state.selectedArtifactId = ''; history.replaceState(null, '', window.location.pathname); renderList();
        });
        root.querySelectorAll('[data-project-tab]').forEach(function (button) {
            button.addEventListener('click', function () {
                state.tab = button.getAttribute('data-project-tab');
                state.selectedArtifactId = '';
                history.replaceState(null, '', projectUrl());
                renderDetail();
            });
        });
        bindWorkflowActions();
        bindArtifactActions();
    }

    function renderTab() {
        if (state.tab === 'overview') return renderOverview();
        if (state.tab === 'ideas') return renderIdeas();
        if (state.tab === 'tasks') return renderTasks();
        if (state.tab === 'artifacts') return renderArtifacts(state.artifacts, '全部产物');
        if (state.tab === 'requirements') return renderArtifacts(state.artifacts.filter(function (item) { return item.type === 'RequirementSpec'; }), '需求规格');
        if (state.tab === 'architecture') return renderArtifacts(state.artifacts.filter(function (item) { return item.type === 'ArchitectureDecision'; }), '架构决策');
        if (state.tab === 'team') return renderTeam();
        if (state.tab === 'activity') return renderActivity();
        if (state.tab === 'settings') return '<section class="og-detail-card"><h2>项目设置</h2><pre class="og-json-preview">' + escapeHtml(JSON.stringify(state.project.settings, null, 2)) + '</pre></section>';
        if (state.tab === 'code') return '<section class="og-detail-card og-code-stage-entry"><div class="og-card-title"><div><h2>进入代码实现阶段</h2>' +
            '<p>Aider 将自动读取本项目的需求规格、架构决策、任务图和审查报告，并结合你上传的项目文件生成真实代码。</p></div>' +
            '<span class="og-status-pill">工作流阶段 4</span></div><div class="og-workflow-config-note">代码页面只需要两项输入：项目文件夹和改动说明。Git、Worktree 和差异控制保留为后台兼容能力，不再要求用户配置。</div>' +
            '<a class="og-primary-button og-code-stage-link" href="code.html?project=' + encodeURIComponent(state.project.id) + '">打开代码实现</a></section>';
        return emptyState('该栏目暂无内容', '这里将显示对应的结构化项目记录。');
    }

    function renderOverview() {
        var project = state.project;
        return '<div class="og-metric-grid"><article><span>活动任务</span><strong>' + project.activeTasks + '</strong></article>' +
            '<article><span>产物</span><strong>' + state.artifacts.length + '</strong></article><article><span>团队角色</span><strong>' + project.team.length + '</strong></article>' +
            '<article><span>工作流运行</span><strong>' + state.workflowRuns.length + '</strong></article></div>' +
            '<div class="og-detail-grid"><section class="og-detail-card"><h2>项目上下文</h2><dl><dt>代码仓库</dt><dd>' + escapeHtml(project.repository || '未关联') +
            '</dd><dt>状态</dt><dd><span class="og-status-pill">' + escapeHtml(label(project.status, statusLabels)) + '</span></dd><dt>最近活动</dt><dd>' + escapeHtml(formatDate(project.lastActivityAt)) +
            '</dd></dl><button class="og-primary-button og-workflow-cta" data-open-workflow-form>' + (latestSourceAnalysis() ? '提出改进需求' : '启动四角色工作流') + '</button></section><section class="og-detail-card"><h2>最近活动</h2>' + activityItems(state.activity.slice(0, 4)) + '</section></div>' + renderSourceAnalysisEntry() + renderRecentWorkflow();
    }

    function latestSourceAnalysis() {
        return state.sourceAnalyses[0] || null;
    }

    function analysisList(title, items) {
        if (!items || !items.length) return '';
        return '<div><b>' + escapeHtml(title) + '</b><ul>' + items.map(function (item) { return '<li>' + escapeHtml(item) + '</li>'; }).join('') + '</ul></div>';
    }

    function renderSourceAnalysis() {
        var analysis = latestSourceAnalysis();
        if (!analysis) return '';
        return '<section class="og-detail-card og-source-analysis"><div class="og-card-title"><div><span class="og-status-pill">智能体项目理解</span><h2>' + escapeHtml(analysis.projectType || '现有软件项目') + '</h2></div>' +
            '<small>' + escapeHtml(analysis.providerId) + ' / ' + escapeHtml(analysis.modelId) + '</small></div><p class="og-source-analysis-summary">' + escapeHtml(analysis.summary) + '</p>' +
            '<div class="og-source-analysis-grid">' + analysisList('技术栈', analysis.technologies) + analysisList('当前架构', analysis.architecture) + analysisList('主要能力', analysis.mainFeatures) + analysisList('已识别风险', analysis.risks) + analysisList('建议关注', analysis.suggestedFocus) + '</div>' +
            '<button class="og-primary-button" data-open-workflow-form>基于该项目提出改进需求</button></section>';
    }

    function renderSourceAnalysisEntry() {
        var analysis = latestSourceAnalysis();
        if (analysis) return renderSourceAnalysis();
        var source = state.sources.find(function (item) { return item.fileCount > 0; });
        if (!source) return '';
        if (!state.capabilities.projectSourceAnalysis) return '<section class="og-detail-card og-source-analysis-pending"><div><span class="og-status-pill">已导入源码</span><h2>项目分析将在服务重启后可用</h2><p>源码已经安全导入；当前运行中的后端仍是旧版本。</p></div></section>';
        return '<section class="og-detail-card og-source-analysis-pending"><div><span class="og-status-pill">已导入源码</span><h2>让智能体先理解这个项目</h2><p>将读取文件树、说明文档、依赖清单和有限源码摘录，生成项目概述后再进入需求流程。</p></div>' +
            '<button class="og-primary-button" data-analyze-source="' + escapeHtml(source.id) + '">分析现有项目</button></section>';
    }

    function renderRecentWorkflow() {
        if (!state.workflowRuns.length) return '';
        var run = state.workflowRuns[0];
        return '<section class="og-detail-card og-workflow-summary"><div class="og-card-title"><h2>最近工作流</h2><a href="workflows.html?runId=' + encodeURIComponent(run.runId) + '">打开时间线</a></div>' +
            '<div class="og-record-list"><article><div><b>' + escapeHtml(run.name) + '</b><p>' + escapeHtml(run.currentPhase || '等待启动') + '</p></div><div class="og-record-meta"><span class="og-status-pill">' + escapeHtml(label(run.status, statusLabels)) + '</span><code>' + escapeHtml(run.runId.slice(0, 12)) + '</code></div></article></div></section>';
    }

    function renderIdeas() {
        var enabled = Boolean(state.capabilities.workflowExecution);
        var analysis = latestSourceAnalysis();
        return (analysis ? renderSourceAnalysis() : '') + '<section class="og-detail-card og-workflow-launch"><div class="og-card-title"><div><h2>' + (analysis ? '提出项目改进需求' : '从 Idea 启动真实协作') + '</h2><p>' + (analysis ? '系统会把上方项目分析作为当前状态，依次生成改进需求、架构决策、实施计划和独立审查。' : '系统将依次调用产品经理、解决方案架构师、技术负责人和审查员，并生成四类结构化产物。') + '</p></div>' +
            '<span class="og-status-pill">' + (enabled ? '已连接真实模型运行时' : '尚未配置真实模型') + '</span></div>' +
            '<form id="workflow-launch-form"><label>运行名称<input name="name" maxlength="300" placeholder="' + (analysis ? '例如：改进登录性能和错误处理' : '例如：企业知识库助手方案评估') + '"></label>' +
            '<label>' + (analysis ? '希望改进什么' : '产品 Idea') + '<textarea name="idea" maxlength="20000" rows="8" required placeholder="' + (analysis ? '请描述要新增、修复或重构的内容，以及期望结果和限制。' : '描述目标用户、核心问题、期望能力和主要约束。') + '"></textarea></label>' +
            (!enabled ? '<div class="og-workflow-config-note">当前服务未启用真实工作流。请在 PyCharm 运行配置中设置 <code>OXYGENT_ENABLE_REAL_WORKFLOW=1</code> 以及四个角色的 Provider、模型和 API Key 环境变量，然后重启服务。</div>' : '') +
            '<p class="og-form-error" id="workflow-form-error" role="alert"></p><div class="og-modal-actions"><button type="submit" class="og-primary-button"' + (enabled ? '' : ' disabled') + '>启动工作流</button></div></form></section>' + renderRecentWorkflow();
    }

    function bindWorkflowActions() {
        root.querySelectorAll('[data-open-workflow-form]').forEach(function (button) {
            button.addEventListener('click', function () {
                state.tab = 'ideas';
                state.selectedArtifactId = '';
                history.replaceState(null, '', projectUrl());
                renderDetail();
            });
        });
        var form = document.getElementById('workflow-launch-form');
        if (form) form.addEventListener('submit', startWorkflow);
        var analyze = root.querySelector('[data-analyze-source]');
        if (analyze) analyze.addEventListener('click', analyzeExistingProject);
    }

    async function analyzeExistingProject(event) {
        var button = event.currentTarget;
        button.disabled = true;
        button.textContent = '智能体正在分析…';
        try {
            var result = await api.analyzeSourceWorkspace(state.project.id, button.getAttribute('data-analyze-source'));
            if (!state.project.description) await api.updateProject(state.project.id, {description: result.analysis.summary});
            state.notice = '项目分析已完成。请查看概述，然后提出改进需求。';
            await openProject(state.project.id);
        } catch (error) {
            state.notice = '项目分析失败：' + error.message;
            renderDetail();
        }
    }

    async function startWorkflow(event) {
        event.preventDefault();
        var form = event.currentTarget;
        var submit = form.querySelector('[type="submit"]');
        var error = document.getElementById('workflow-form-error');
        var data = new FormData(form);
        submit.disabled = true;
        error.textContent = '';
        try {
            var result = await api.startProjectWorkflow(state.project.id, {
                name: data.get('name').trim(),
                idea: data.get('idea').trim(),
                sourceWorkspaceId: latestSourceAnalysis() ? latestSourceAnalysis().sourceWorkspaceId : null,
                sourceAnalysisId: latestSourceAnalysis() ? latestSourceAnalysis().id : null
            });
            window.location.href = 'workflows.html?runId=' + encodeURIComponent(result.run.runId);
        } catch (requestError) {
            error.textContent = requestError.message;
            submit.disabled = false;
        }
    }

    function renderTasks() {
        if (!state.tasks.length) return emptyState('暂无项目任务', '可在对话页面使用“转换为项目任务”创建第一个关联追踪的任务。');
        return '<section class="og-detail-card"><div class="og-card-title"><h2>项目任务</h2><span>共 ' + state.tasks.length + ' 个</span></div><div class="og-record-list">' +
            state.tasks.map(function (task) { return '<article><div><b>' + escapeHtml(task.title) + '</b><p>' + escapeHtml(task.objective) + '</p></div>' +
                '<div class="og-record-meta"><span class="og-status-pill">' + escapeHtml(label(task.status, statusLabels)) + '</span><span>' + escapeHtml(label(task.risk, riskLabels)) + '</span>' +
                (task.sourceTraceId ? '<code>追踪 ' + escapeHtml(task.sourceTraceId.slice(0, 12)) + '</code>' : '<span>无追踪记录</span>') + '</div></article>'; }).join('') + '</div></section>';
    }

    function renderArtifacts(items, title) {
        if (!items.length) {
            var emptyTitle = title === '全部产物' ? '暂无产物' : '暂无' + title;
            return emptyState(emptyTitle, '请运行结构化角色工作流，或向项目关联已验证的产物。');
        }
        var selected = items.find(function (artifact) { return artifact.id === state.selectedArtifactId; });
        var detail = selected ? renderArtifactDetail(selected) : '';
        return detail + '<section class="og-detail-card"><div class="og-card-title"><h2>' + escapeHtml(title) + '</h2><span>仅追加修订</span></div><div class="og-artifact-grid">' +
            items.map(function (artifact) {
                var summary = artifact.content && artifact.content.summary ? artifact.content.summary : '结构化产物';
                var compact = String(summary).replace(/\s+/g, ' ').trim();
                if (compact.length > 180) compact = compact.slice(0, 177) + '…';
                return '<button type="button" class="og-artifact-card' + (artifact.id === state.selectedArtifactId ? ' selected' : '') + '" data-artifact-id="' + escapeHtml(artifact.id) + '"><div class="og-artifact-type">' +
                    escapeHtml(label(artifact.type, artifactLabels)) + '</div><h3>' + escapeHtml(compact) + '</h3><p>修订 ' + artifact.revision + ' · ' + escapeHtml(label(artifact.validationStatus, statusLabels)) + '</p>' +
                    '<div class="og-artifact-provenance"><span>' + escapeHtml(label(artifact.producerRole, roleLabels)) + '</span><span>' + escapeHtml(artifact.providerId) + ' / ' + escapeHtml(artifact.modelId) + '</span></div></button>';
            }).join('') + '</div></section>';
    }

    function artifactList(title, items) {
        if (!items || !items.length) return '';
        return '<section class="og-artifact-section"><h3>' + escapeHtml(title) + '</h3><ul>' + items.map(function (item) {
            return '<li>' + escapeHtml(item) + '</li>';
        }).join('') + '</ul></section>';
    }

    function renderArtifactDetail(artifact) {
        var content = artifact.content || {};
        var sections = '';
        if (artifact.type === 'RequirementSpec') {
            sections += artifactList('需求', content.requirements);
            sections += artifactList('约束', content.constraints);
            sections += artifactList('验收标准', content.acceptanceCriteria);
        } else if (artifact.type === 'ArchitectureDecision') {
            sections += artifactList('架构决策', content.decisions);
            sections += artifactList('约束', content.constraints);
            sections += artifactList('影响与后果', content.consequences);
        } else if (artifact.type === 'TaskGraph') {
            sections += '<section class="og-artifact-section"><h3>任务</h3><div class="og-artifact-tasks">' + (content.tasks || []).map(function (task) {
                return '<article><b>' + escapeHtml(task.title) + '</b>' + (task.description ? '<p>' + escapeHtml(task.description) + '</p>' : '') +
                    '<small>' + (task.dependsOn && task.dependsOn.length ? '依赖：' + escapeHtml(task.dependsOn.join('、')) : '无前置依赖') + '</small></article>';
            }).join('') + '</div></section>';
        } else if (artifact.type === 'ReviewReport') {
            sections += '<section class="og-artifact-section"><h3>审查结论</h3><p>' + (content.approved == null ? '未给出批准结论' : content.approved ? '建议通过' : '需要修订') + '</p></section>';
            sections += '<section class="og-artifact-section"><h3>发现</h3><div class="og-artifact-findings">' + (content.findings || []).map(function (finding) {
                return '<article><span>' + escapeHtml(finding.severity || 'info') + '</span><p>' + escapeHtml(finding.message) + '</p></article>';
            }).join('') + '</div></section>';
        }
        return '<section class="og-detail-card og-artifact-detail"><div class="og-card-title"><div><span class="og-artifact-type">' + escapeHtml(label(artifact.type, artifactLabels)) + '</span><h2>' + escapeHtml(content.summary || '结构化产物') + '</h2></div><button type="button" class="og-secondary-button" data-close-artifact>关闭详情</button></div>' +
            '<div class="og-artifact-meta"><span>修订 ' + artifact.revision + '</span><span>' + escapeHtml(label(artifact.validationStatus, statusLabels)) + '</span><span>' + escapeHtml(label(artifact.producerRole, roleLabels)) + '</span><span>' + escapeHtml(formatDate(artifact.createdAt)) + '</span></div>' +
            sections + '</section>';
    }

    function bindArtifactActions() {
        root.querySelectorAll('[data-artifact-id]').forEach(function (button) {
            button.addEventListener('click', function () {
                state.selectedArtifactId = button.getAttribute('data-artifact-id');
                state.tab = 'artifacts';
                history.replaceState(null, '', projectUrl());
                renderDetail();
            });
        });
        var close = root.querySelector('[data-close-artifact]');
        if (close) close.addEventListener('click', function () {
            state.selectedArtifactId = '';
            history.replaceState(null, '', projectUrl());
            renderDetail();
        });
    }

    function renderTeam() {
        if (!state.project.team.length) return emptyState('尚未分配团队', '请在项目设置中分配角色。');
        return '<section class="og-detail-card"><h2>已分配角色</h2><div class="og-team-list">' + state.project.team.map(function (role) {
            var displayRole = label(role, roleLabels); return '<article><span class="og-role-mark">' + escapeHtml(displayRole.charAt(0)) + '</span><div><b>' + escapeHtml(displayRole) + '</b><p>模型策略映射可在智能体页面查看。</p></div></article>';
        }).join('') + '</div></section>';
    }

    function activityItems(items) {
        if (!items.length) return '<p class="og-muted-copy">暂无活动记录。</p>';
        return '<div class="og-activity-list">' + items.map(function (item) {
            var summary = item.summary;
            if (item.eventType === 'project.created') summary = '项目已创建';
            if (item.eventType === 'project.updated') summary = '项目已更新';
            if (item.eventType === 'task.createdFromChat') summary = summary.replace(/^Task created from Chat:\s*/, '已从对话创建任务：');
            if (item.eventType === 'workflow.started') summary = summary.replace(/^四角色工作流已启动：/, '四角色工作流已启动：');
            if (item.eventType === 'workflow.completed') summary = summary.replace(/^四角色工作流已完成：/, '四角色工作流已完成：');
            if (item.eventType === 'workflow.awaitingImplementation') summary = summary.replace(/^规划工作流已完成，等待实现：/, '规划已完成，等待实现：');
            if (item.eventType === 'workflow.failed') summary = summary.replace(/^四角色工作流执行失败：/, '四角色工作流执行失败：');
            return '<article><span></span><div><b>' + escapeHtml(summary) + '</b><small>' + escapeHtml(formatDate(item.createdAt)) + '</small></div></article>';
        }).join('') + '</div>';
    }

    function renderActivity() {
        return '<section class="og-detail-card"><h2>活动</h2>' + activityItems(state.activity) + '</section>';
    }

    function renderError(error) {
        root.innerHTML = header('项目') + '<div class="og-workspace-content">' + emptyState('项目 API 尚未配置', '请确认 OxyGent 已加载平台路由。' + error.message) + '</div>';
        var button = document.getElementById('create-project-button');
        if (button) button.disabled = true;
    }

    async function loadProjects() {
        var results = await Promise.all([api.listProjects(), api.capabilities()]);
        state.projects = results[0].items || [];
        state.capabilities = results[1].capabilities || {};
    }

    async function mount() {
        root.innerHTML = header('项目') + '<div class="og-workspace-content"><div class="og-loading-state">正在加载项目…</div></div>';
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
