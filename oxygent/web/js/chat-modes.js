(function () {
    'use strict';

    var prompts = {
        'Fix a bug': 'Fix a bug in the selected repository. Start by clarifying the observed behavior and acceptance criteria.',
        'Add a feature': 'Add a feature to the selected repository. Start by defining requirements and acceptance criteria.',
        'Refactor code': 'Refactor code in the selected repository while preserving behavior. Start by identifying scope and verification.',
        'Write tests': 'Write tests for the selected repository. Start by identifying behavior, risk, and missing coverage.',
        'Review changes': 'Review the selected changes for correctness, risk, maintainability, and verification gaps.',
        'Explain repository': 'Explain the selected repository structure, important modules, dependencies, and test strategy.'
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
                ? 'Describe the code task. Repository execution is enabled in PR 5.'
                : 'Ask me anything here.';
        }
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
        return 'Continue this Chat as a structured Project Task.';
    }

    function modalMarkup() {
        return '<div class="og-modal-backdrop" id="chat-project-modal" hidden><section class="og-modal" role="dialog" aria-modal="true" aria-labelledby="chat-project-title">' +
            '<div class="og-modal-header"><div><p class="og-workspace-eyebrow">Trace-linked handoff</p><h2 id="chat-project-title">Convert to Project Task</h2></div>' +
            '<button type="button" class="og-icon-button" data-close-chat-project aria-label="Close">×</button></div>' +
            '<form id="chat-project-form"><label>Project<select name="projectId" id="chat-project-select" required></select></label>' +
            '<label>Task title<input name="title" maxlength="200" required></label>' +
            '<label>Objective<textarea name="objective" maxlength="4000" rows="4" required></textarea></label>' +
            '<div><p class="og-field-label">Source Artifacts</p><div class="chat-artifact-options" id="chat-artifact-options"><span>Select a Project to load Artifacts.</span></div></div>' +
            '<p class="chat-reference-note" id="chat-reference-note"></p><p class="og-form-error" id="chat-project-error" role="alert"></p>' +
            '<div class="og-modal-actions"><button type="button" class="og-secondary-button" data-close-chat-project>Cancel</button>' +
            '<button type="submit" class="og-primary-button">Create Task</button></div></form></section></div>';
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
            button.title = platformProjects.length ? 'Create a trace-linked Project Task' : 'Create a Project first';
        } catch (_error) {
            button.disabled = true;
            button.title = 'Projects API is not configured';
        }
    }

    function setOptions(select, items, valueKey, label) {
        select.innerHTML = items.length ? items.map(function (item) {
            return '<option value="' + escapeHtml(item[valueKey]) + '">' + escapeHtml(label(item)) + '</option>';
        }).join('') : '<option>No options configured</option>';
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
            if (!responses[0].capabilities.codeWorkspace) throw new Error('Code Workspace is not configured');
            codeRepositories = responses[1].items || [];
            setOptions(repositorySelect, codeRepositories, 'id', function (item) { return item.name; });
            setOptions(workflowSelect, responses[2].items || [], 'runId', function (item) { return item.name; });
            var agents = responses[3].items || [];
            var teams = agents.length ? [{id: 'configured-team', name: agents.length + '-role configured team'}] : [];
            setOptions(teamSelect, teams, 'id', function (item) { return item.name; });
            function updateBranches() {
                var repository = codeRepositories.find(function (item) { return item.id === repositorySelect.value; });
                var branches = repository ? repository.allowedBaseBranches.map(function (name) { return {name: name}; }) : [];
                setOptions(branchSelect, branches, 'name', function (item) { return item.name; });
            }
            repositorySelect.addEventListener('change', updateBranches);
            updateBranches();
        } catch (error) {
            repositorySelect.innerHTML = '<option>Configure in Code Workspace</option>';
            document.getElementById('code-mode-note').textContent = error.message + '. General Chat remains available.';
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
        document.getElementById('chat-reference-note').textContent = (hasTrace ? 'Trace linked' : 'No trace available') +
            ' · ' + references.length + ' attachment reference' + (references.length === 1 ? '' : 's') +
            ' · the full transcript is not copied';
        modal.hidden = false;
        await loadArtifactOptions();
    }

    async function loadArtifactOptions() {
        var projectId = document.getElementById('chat-project-select').value;
        var container = document.getElementById('chat-artifact-options');
        if (!projectId) return;
        container.innerHTML = '<span>Loading Artifacts…</span>';
        try {
            var data = await window.OxyGentApp.api.listArtifacts(projectId, true);
            var artifacts = data.items || [];
            container.innerHTML = artifacts.length ? artifacts.map(function (artifact) {
                var summary = artifact.content && artifact.content.summary ? artifact.content.summary : artifact.type;
                return '<label><input type="checkbox" name="artifactId" value="' + escapeHtml(artifact.id) + '"><span><b>' + escapeHtml(artifact.type) +
                    '</b><small>' + escapeHtml(summary) + '</small></span></label>';
            }).join('') : '<span>No Artifacts in this Project.</span>';
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
        toast.innerHTML = '<div><b>Project Task created</b><span>' + escapeHtml(task.title) + '</span></div>' +
            '<a href="projects.html?project=' + encodeURIComponent(task.projectId) + '&tab=tasks">Open Task</a>';
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
        selectMode('general');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
    } else {
        mount();
    }
})();
