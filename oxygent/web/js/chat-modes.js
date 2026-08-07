(function () {
    'use strict';

    var prompts = {
        '修复缺陷': '修复所选仓库中的缺陷。请先明确已观察到的行为和验收标准。',
        '添加功能': '向所选仓库添加功能。请先定义需求和验收标准。',
        '重构代码': '在保持现有行为的前提下重构所选仓库。请先明确范围和验证方式。',
        '编写测试': '为所选仓库编写测试。请先识别目标行为、风险和覆盖缺口。',
        '审查变更': '审查所选变更的正确性、风险、可维护性和验证缺口。',
        '解释仓库': '解释所选仓库的结构、重要模块、依赖关系和测试策略。'
    };
    var platformProjects = [];
    var codeRepositories = [];

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function selectMode(mode) {
        document.querySelectorAll('.chat-mode-button').forEach(function (button) {
            var active = button.getAttribute('data-chat-mode') === mode;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        var codePanel = document.getElementById('code-mode-panel');
        if (codePanel) codePanel.classList.toggle('visible', mode === 'code');
        var input = document.getElementById('message_input');
        if (input) {
            input.placeholder = mode === 'code'
                ? '请描述代码任务。仓库操作会在隔离工作树中执行。'
                : '请在这里输入你的问题。';
        }
    }

    function syncConversationState() {
        var chatbox = document.getElementById('chatbox');
        if (!chatbox) return;
        var hasUserMessage = Boolean(chatbox.querySelector('li.me'));
        document.body.classList.toggle('chat-has-conversation', hasUserMessage);
        if (!hasUserMessage) {
            var history = document.getElementById('chat_history');
            if (history) window.requestAnimationFrame(function () { history.scrollTop = 0; });
        }
    }

    function mountChatStarters() {
        document.querySelectorAll('[data-chat-prompt]').forEach(function (button) {
            button.addEventListener('click', function () {
                var input = document.getElementById('message_input');
                if (!input) return;
                input.value = button.getAttribute('data-chat-prompt') || '';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.focus();
            });
        });
        var chatbox = document.getElementById('chatbox');
        if (!chatbox) return;
        syncConversationState();
        new MutationObserver(syncConversationState).observe(chatbox, {childList: true, subtree: false});
    }

    function currentObjective() {
        var input = document.getElementById('message_input');
        if (input && input.value.trim()) return input.value.trim().slice(0, 4000);
        if (typeof chats !== 'undefined' && Array.isArray(chats)) {
            for (var index = chats.length - 1; index >= 0; index -= 1) {
                if (chats[index].role === 'user' && typeof chats[index].content === 'string') {
                    return chats[index].content.slice(0, 4000);
                }
            }
        }
        return '将当前对话继续整理为结构化项目任务。';
    }

    function modalMarkup() {
        return '<div class="og-modal-backdrop" id="chat-project-modal" hidden><section class="og-modal" role="dialog" aria-modal="true" aria-labelledby="chat-project-title">' +
            '<div class="og-modal-header"><div><p class="og-workspace-eyebrow">关联追踪的任务交接</p><h2 id="chat-project-title">转换为项目任务</h2></div>' +
            '<button type="button" class="og-icon-button" data-close-chat-project aria-label="关闭">×</button></div>' +
            '<form id="chat-project-form"><label>项目<select name="projectId" id="chat-project-select" required></select></label>' +
            '<label>任务标题<input name="title" maxlength="200" required></label>' +
            '<label>目标<textarea name="objective" maxlength="4000" rows="4" required></textarea></label>' +
            '<div><p class="og-field-label">来源产物</p><div class="chat-artifact-options" id="chat-artifact-options"><span>请选择项目以加载产物。</span></div></div>' +
            '<p class="chat-reference-note" id="chat-reference-note"></p><p class="og-form-error" id="chat-project-error" role="alert"></p>' +
            '<div class="og-modal-actions"><button type="button" class="og-secondary-button" data-close-chat-project>取消</button>' +
            '<button type="submit" class="og-primary-button">创建任务</button></div></form></section></div>';
    }

    async function mountProjectTaskConversion() {
        var button = document.getElementById('convert-project-task');
        if (!button || !window.OxyGentApp || !window.OxyGentApp.api) return;
        document.body.insertAdjacentHTML('beforeend', modalMarkup());
        document.querySelectorAll('[data-close-chat-project]').forEach(function (close) {
            close.addEventListener('click', function () {
                document.getElementById('chat-project-modal').hidden = true;
            });
        });
        document.getElementById('chat-project-select').addEventListener('change', loadArtifactOptions);
        document.getElementById('chat-project-form').addEventListener('submit', submitProjectTask);
        button.addEventListener('click', openProjectTaskModal);
        try {
            var data = await window.OxyGentApp.api.listProjects();
            platformProjects = data.items || [];
            button.disabled = platformProjects.length === 0;
            button.title = platformProjects.length ? '创建关联追踪的项目任务' : '请先创建项目';
        } catch (_error) {
            button.disabled = true;
            button.title = '项目 API 尚未配置';
        }
    }

    function setOptions(select, items, valueKey, label) {
        select.innerHTML = items.length ? items.map(function (item) {
            return '<option value="' + escapeHtml(item[valueKey]) + '">' + escapeHtml(label(item)) + '</option>';
        }).join('') : '<option>暂无可用选项</option>';
        select.disabled = items.length === 0;
    }

    async function mountCodeSelectors() {
        if (!window.OxyGentApp || !window.OxyGentApp.api) return;
        var repositorySelect = document.getElementById('code-repository');
        var branchSelect = document.getElementById('code-base-branch');
        var workflowSelect = document.getElementById('code-workflow');
        var teamSelect = document.getElementById('code-agent-team');
        if (!repositorySelect || !branchSelect || !workflowSelect || !teamSelect) return;
        try {
            var responses = await Promise.all([
                window.OxyGentApp.api.capabilities(),
                window.OxyGentApp.api.listRepositories(),
                window.OxyGentApp.api.listWorkflowRuns(),
                window.OxyGentApp.api.listAgents()
            ]);
            if (!responses[0].capabilities.codeWorkspace) throw new Error('代码工作区尚未配置');
            codeRepositories = responses[1].items || [];
            setOptions(repositorySelect, codeRepositories, 'id', function (item) { return item.name; });
            setOptions(workflowSelect, responses[2].items || [], 'runId', function (item) { return item.name; });
            var agents = responses[3].items || [];
            var teams = agents.length ? [{id: 'configured-team', name: '已配置 ' + agents.length + ' 个角色的团队'}] : [];
            setOptions(teamSelect, teams, 'id', function (item) { return item.name; });
            function updateBranches() {
                var repository = codeRepositories.find(function (item) { return item.id === repositorySelect.value; });
                var branches = repository ? repository.allowedBaseBranches.map(function (name) { return {name: name}; }) : [];
                setOptions(branchSelect, branches, 'name', function (item) { return item.name; });
            }
            repositorySelect.addEventListener('change', updateBranches);
            updateBranches();
        } catch (error) {
            repositorySelect.innerHTML = '<option>请在代码工作区中配置</option>';
            document.getElementById('code-mode-note').textContent = error.message + '。通用对话仍可正常使用。';
        }
    }

    async function openProjectTaskModal() {
        var modal = document.getElementById('chat-project-modal');
        var select = document.getElementById('chat-project-select');
        var objective = currentObjective();
        select.innerHTML = platformProjects.map(function (project) {
            return '<option value="' + escapeHtml(project.id) + '">' + escapeHtml(project.name) + '</option>';
        }).join('');
        document.querySelector('#chat-project-form [name="title"]').value = objective.slice(0, 80);
        document.querySelector('#chat-project-form [name="objective"]').value = objective;
        document.getElementById('chat-project-error').textContent = '';
        var references = typeof projectTaskAttachmentReferences === 'undefined' ? [] : projectTaskAttachmentReferences;
        var hasTrace = typeof from_trace_id !== 'undefined' && from_trace_id;
        document.getElementById('chat-reference-note').textContent = (hasTrace ? '已关联追踪记录' : '暂无可用追踪记录') +
            ' · ' + references.length + ' 个附件引用 · 不会复制完整对话内容';
        modal.hidden = false;
        await loadArtifactOptions();
    }

    async function loadArtifactOptions() {
        var projectId = document.getElementById('chat-project-select').value;
        var container = document.getElementById('chat-artifact-options');
        if (!projectId) return;
        container.innerHTML = '<span>正在加载产物…</span>';
        try {
            var data = await window.OxyGentApp.api.listArtifacts(projectId, true);
            var artifacts = data.items || [];
            container.innerHTML = artifacts.length ? artifacts.map(function (artifact) {
                var summary = artifact.content && artifact.content.summary ? artifact.content.summary : artifact.type;
                return '<label><input type="checkbox" name="artifactId" value="' + escapeHtml(artifact.id) + '"><span><b>' + escapeHtml(artifact.type) +
                    '</b><small>' + escapeHtml(summary) + '</small></span></label>';
            }).join('') : '<span>该项目中暂无产物。</span>';
        } catch (error) {
            container.innerHTML = '<span>' + escapeHtml(error.message) + '</span>';
        }
    }

    async function submitProjectTask(event) {
        event.preventDefault();
        var form = event.currentTarget;
        var data = new FormData(form);
        var submit = form.querySelector('[type="submit"]');
        var error = document.getElementById('chat-project-error');
        submit.disabled = true;
        error.textContent = '';
        try {
            var references = typeof projectTaskAttachmentReferences === 'undefined' ? [] : projectTaskAttachmentReferences.slice();
            var traceId = typeof from_trace_id === 'undefined' || !from_trace_id ? null : from_trace_id;
            var result = await window.OxyGentApp.api.createTaskFromChat(data.get('projectId'), {
                title: data.get('title').trim(),
                objective: data.get('objective').trim(),
                taskType: 'general',
                risk: 'medium',
                sourceTraceId: traceId,
                attachmentReferences: references,
                sourceArtifactIds: data.getAll('artifactId')
            });
            document.getElementById('chat-project-modal').hidden = true;
            showConversionToast(result.task);
        } catch (requestError) {
            error.textContent = requestError.message;
        } finally {
            submit.disabled = false;
        }
    }

    function showConversionToast(task) {
        var existing = document.getElementById('chat-project-toast');
        if (existing) existing.remove();
        var toast = document.createElement('div');
        toast.className = 'chat-project-toast';
        toast.id = 'chat-project-toast';
        toast.innerHTML = '<div><b>项目任务已创建</b><span>' + escapeHtml(task.title) + '</span></div>' +
            '<a href="projects.html?project=' + encodeURIComponent(task.projectId) + '&tab=tasks">打开任务</a>';
        document.body.appendChild(toast);
        window.setTimeout(function () { if (toast.parentNode) toast.remove(); }, 8000);
    }

    function mount() {
        document.querySelectorAll('.chat-mode-button').forEach(function (button) {
            button.addEventListener('click', function () {
                selectMode(button.getAttribute('data-chat-mode'));
            });
        });
        document.querySelectorAll('.code-quick-action').forEach(function (button) {
            button.addEventListener('click', function () {
                var input = document.getElementById('message_input');
                if (!input) return;
                input.value = prompts[button.textContent.trim()] || '';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.focus();
            });
        });
        mountProjectTaskConversion();
        mountCodeSelectors();
        mountChatStarters();
        selectMode('general');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
    } else {
        mount();
    }
})();
