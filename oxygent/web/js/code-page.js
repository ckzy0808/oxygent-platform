(function () {
    'use strict';

    var api;
    var state = {projects: [], sources: [], repositories: [], tasks: [], project: null, task: null, diff: null, profiles: [], runs: [], approvals: []};
    var phases = ['Requirement', 'Architecture', 'Plan', 'Implementation', 'Verification', 'Review', 'Approval'];

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function formatDate(value) {
        return value ? new Date(value).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : '—';
    }

    function modal(id, title, body, submitLabel) {
        return '<div class="og-modal-backdrop" id="' + id + '" hidden><section class="og-modal code-modal" role="dialog" aria-modal="true">' +
            '<div class="og-modal-header"><div><p class="og-workspace-eyebrow">Code Workspace</p><h2>' + escapeHtml(title) + '</h2></div>' +
            '<button type="button" class="og-icon-button" data-close-modal="' + id + '">×</button></div>' +
            '<form data-modal-form="' + id + '">' + body + '<p class="og-form-error" data-modal-error></p>' +
            '<div class="og-modal-actions"><button type="button" class="og-secondary-button" data-close-modal="' + id + '">Cancel</button>' +
            '<button type="submit" class="og-primary-button">' + escapeHtml(submitLabel) + '</button></div></form></section></div>';
    }

    function shell() {
        return '<header class="og-workspace-header code-header"><div><p class="og-workspace-eyebrow">Isolated engineering workspace</p>' +
            '<h1 class="og-workspace-title">Code</h1></div><div class="code-header-actions"><span class="og-preview-badge">Worktree protected</span>' +
            '<button class="og-secondary-button" id="register-repository">Register repository</button><button class="og-primary-button" id="new-code-task">New Code Task</button></div></header>' +
            '<div class="og-workspace-content code-content"><div class="code-toolbar"><label>Project<select id="code-project-select"></select></label>' +
            '<label>Code Task<select id="code-task-select"></select></label><div class="code-security-note"><b>Source workspace protected</b><span>Read operations target only the linked task worktree.</span></div></div>' +
            '<div id="code-message" class="code-message" hidden></div><section class="code-layout" id="code-layout"></section></div>' +
            modal('repository-modal', 'Register approved repository',
                '<label>Project<select name="projectId" required></select></label><label>Approved source<select name="rootReference" required></select></label>' +
                '<label>Display name<input name="name" maxlength="160" required></label><div class="code-form-grid"><label>Default branch<input name="defaultBranch" value="main" required></label>' +
                '<label>Allowed base branches<input name="allowedBranches" value="main" required></label></div>' +
                '<p class="code-form-help">The browser selects an opaque server-approved source reference, never a filesystem path.</p>', 'Register') +
            modal('task-modal', 'Create isolated Code Task',
                '<label>Repository<select name="repositoryId" required></select></label><label>Base branch<select name="baseBranch" required></select></label>' +
                '<label>Objective<textarea name="objective" rows="3" maxlength="4000" required></textarea></label>' +
                '<label>Acceptance criteria<textarea name="criteria" rows="3" required>Requested behavior is implemented\nExisting behavior remains compatible\nConfigured verification passes</textarea></label>' +
                '<div class="code-form-grid"><label>Allowed paths<input name="allowedPaths" value="oxygent/**, tests/**, docs/**, examples/**" required></label>' +
                '<label>Forbidden paths<input name="forbiddenPaths" value=".env*, **/*.key, **/*.pem"></label>' +
                '<label>Max changed files<input name="maxChangedFiles" type="number" value="20" min="1" max="1000"></label>' +
                '<label>Max diff lines<input name="maxDiffLines" type="number" value="1000" min="1" max="100000"></label></div>' +
                '<label class="code-checkbox"><input type="checkbox" name="dependencyChangesAllowed"> Allow dependency manifest changes</label>', 'Create worktree');
    }

    function setMessage(message, kind) {
        var target = document.getElementById('code-message');
        target.hidden = !message;
        target.className = 'code-message ' + (kind || 'info');
        target.textContent = message || '';
    }

    function options(items, value, label, empty) {
        return items.length ? items.map(function (item) {
            return '<option value="' + escapeHtml(value(item)) + '">' + escapeHtml(label(item)) + '</option>';
        }).join('') : '<option value="">' + escapeHtml(empty) + '</option>';
    }

    function renderEmpty() {
        document.getElementById('code-layout').innerHTML = '<section class="code-empty"><div class="code-empty-icon">⌘</div><h2>No isolated Code Task selected</h2>' +
            '<p>Register an administrator-approved repository, then create a task. OxyGent will resolve the base commit and create a separate Git worktree.</p>' +
            '<div class="code-empty-guards"><span>✓ No arbitrary repository paths</span><span>✓ Server-generated task branch</span><span>✓ System-enforced Change Contract</span></div></section>';
    }

    function renderTaskShell(task) {
        var contract = task.changeContract;
        var activeIndex = task.changedFiles && task.changedFiles.length ? 3 : 2;
        var stages = phases.map(function (phase, index) {
            var status = index < activeIndex ? 'complete' : (index === activeIndex ? 'active' : 'pending');
            return '<li class="code-phase ' + status + '"><span class="code-phase-dot"></span><div><b>' + phase + '</b><small>' +
                (status === 'complete' ? 'Completed' : status === 'active' ? 'Ready in isolated worktree' : 'Not started') + '</small></div></li>';
        }).join('');
        document.getElementById('code-layout').innerHTML =
            '<aside class="code-pane repository-pane"><div class="code-pane-title"><div><span>Repository Context</span><b id="repository-name">Loading…</b></div><span class="code-status ready">Isolated</span></div>' +
            '<dl class="code-metadata" id="repository-metadata"><div><dt>Branch</dt><dd>' + escapeHtml(task.branch) + '</dd></div><div><dt>Base</dt><dd>' + escapeHtml(task.baseCommit.slice(0, 12)) + '</dd></div>' +
            '<div><dt>Worktree</dt><dd title="' + escapeHtml(task.worktreePath) + '">' + escapeHtml(task.worktreePath.split('/').slice(-2).join('/')) + '</dd></div></dl>' +
            '<form class="code-search" id="repository-search"><input name="query" placeholder="Search tracked files" maxlength="500"><button>Search</button></form>' +
            '<div class="code-tree-header"><b>Relevant files</b><span id="tree-count">—</span></div><div class="code-file-list" id="repository-tree"><span class="code-loading">Reading worktree…</span></div></aside>' +
            '<main class="code-pane timeline-pane"><div class="code-pane-title"><div><span>Task Timeline</span><b>' + escapeHtml(contract.objective) + '</b></div><span class="code-status ready">' + escapeHtml(task.status) + '</span></div>' +
            '<ol class="code-phase-list">' + stages + '</ol><div class="code-safety-card"><b>Mutation remains gated</b><p>PR5 exposes repository read, tree, search, metadata, and isolated worktree creation. It does not expose an Agent file-write API.</p></div></main>' +
            '<aside class="code-pane contract-pane"><nav class="code-result-tabs" aria-label="Changes and Verification">' +
            ['Summary', 'Changes', 'Diff', 'Verification', 'Review', 'Artifacts'].map(function (label, index) { return '<button data-code-tab="' + label.toLowerCase() + '" class="' + (index === 0 ? 'active' : '') + '">' + label + '</button>'; }).join('') +
            '</nav><div id="code-tab-content"></div><section class="file-preview" id="file-preview" hidden><div><b id="preview-path"></b><button id="close-preview">×</button></div><pre id="preview-content"></pre></section></aside>';
        document.getElementById('repository-search').addEventListener('submit', searchRepository);
        document.querySelectorAll('[data-code-tab]').forEach(function (button) {
            button.addEventListener('click', function () { renderResultTab(button.getAttribute('data-code-tab')); });
        });
        renderResultTab('summary');
    }

    function summaryTab() {
        var contract = state.task.changeContract;
        return '<div class="code-pane-title"><div><span>Change Contract</span><b>Enforced by Scope Guard</b></div><span class="code-risk ' + escapeHtml(contract.risk) + '">' + escapeHtml(contract.risk) + ' risk</span></div>' +
            '<section class="contract-section"><h3>Objective</h3><p>' + escapeHtml(contract.objective) + '</p></section>' +
            '<section class="contract-section"><h3>Acceptance criteria</h3><ul>' + contract.acceptanceCriteria.map(function (item) { return '<li>' + escapeHtml(item) + '</li>'; }).join('') + '</ul></section>' +
            '<section class="contract-section"><h3>Allowed paths</h3><div class="code-tags">' + contract.allowedPaths.map(function (item) { return '<code>' + escapeHtml(item) + '</code>'; }).join('') + '</div></section>' +
            '<section class="contract-section"><h3>Hard limits</h3><dl class="contract-limits"><div><dt>Files</dt><dd>' + contract.maxChangedFiles + '</dd></div><div><dt>Diff lines</dt><dd>' + contract.maxDiffLines + '</dd></div><div><dt>Dependencies</dt><dd>' + (contract.dependencyChangesAllowed ? 'Allowed' : 'Blocked') + '</dd></div></dl></section>';
    }

    function changesTab() {
        var diff = state.diff;
        if (!diff) return '<div class="code-tab-empty">Diff metadata is loading…</div>';
        var files = diff.changedFiles || [];
        return '<div class="code-pane-title"><div><span>Changed files</span><b>' + files.length + ' files · +' + diff.additions + ' / −' + diff.deletions + '</b></div><span class="code-scope-state ' + (diff.scopeStatus === 'valid' ? 'valid' : 'blocked') + '">' + escapeHtml(diff.scopeStatus) + '</span></div>' +
            (files.length ? '<div class="changed-file-list">' + files.map(function (path) { return '<div><code>' + escapeHtml(path) + '</code><span>Modified in worktree</span></div>'; }).join('') + '</div>' : '<div class="code-tab-empty">No changes relative to the recorded base commit.</div>');
    }

    function diffTab() {
        var diff = state.diff;
        if (!diff) return '<div class="code-tab-empty">Unified diff is loading…</div>';
        if (diff.scopeStatus !== 'valid') return '<div class="verification-blocked"><b>Diff blocked by Scope Guard</b><p>' + escapeHtml(diff.scopeStatus.replace(/^blocked:\s*/, '')) + '</p><small>Diff content is withheld when the Change Contract fails.</small></div>';
        return '<div class="code-pane-title"><div><span>Unified diff</span><b>' + diff.diffLineCount + ' changed lines</b></div>' + (diff.truncated ? '<span class="code-scope-state blocked">Truncated</span>' : '<span class="code-scope-state valid">Complete</span>') + '</div>' +
            (diff.diff ? '<pre class="unified-diff">' + escapeHtml(diff.diff) + '</pre>' : '<div class="code-tab-empty">No diff to display.</div>');
    }

    function verificationTab() {
        var commands = [];
        state.profiles.forEach(function (profile) {
            profile.commands.forEach(function (command) { commands.push({profile: profile, command: command}); });
        });
        var commandCards = commands.length ? commands.map(function (item) {
            return '<article class="verification-command"><div><span>' + escapeHtml(item.command.slot) + '</span><b>' + escapeHtml(item.command.name) + '</b><code>' + escapeHtml(item.command.argv.join(' ')) + '</code></div>' +
                '<button class="og-secondary-button" data-run-profile="' + escapeHtml(item.profile.id) + '" data-run-command="' + escapeHtml(item.command.id) + '">Run</button></article>';
        }).join('') : '<div class="code-tab-empty">No Verification Profile is configured for this Project.</div>';
        var runs = state.runs.length ? '<div class="verification-runs"><h3>Recorded results</h3>' + state.runs.map(function (run) {
            return '<article class="verification-run ' + escapeHtml(run.status) + '"><div><span>' + escapeHtml(run.status) + '</span><b>' + escapeHtml(run.commandName) + '</b></div><dl><div><dt>Exit</dt><dd>' + (run.exitCode == null ? '—' : run.exitCode) + '</dd></div><div><dt>Duration</dt><dd>' + Math.round(run.durationMs) + ' ms</dd></div></dl>' +
                '<code>' + escapeHtml(run.argv.join(' ')) + '</code>' + (run.stdoutPreview ? '<pre>' + escapeHtml(run.stdoutPreview) + '</pre>' : '') + '</article>';
        }).join('') + '</div>' : '';
        return '<div class="code-pane-title"><div><span>Verification</span><b>Real commands, exit codes, and output</b></div><span class="code-scope-state valid">Fixed argv</span></div>' + commandCards + runs;
    }

    function reviewTab() {
        var task = state.task;
        var approvalState = task.approvalState || 'draft';
        var history = state.approvals.length ? '<div class="approval-history"><h3>Immutable audit</h3>' + state.approvals.map(function (record) {
            return '<article><span>' + escapeHtml(record.action) + '</span><b>' + escapeHtml(record.actorId) + '</b><small>' + formatDate(record.createdAt) + (record.reason ? ' · ' + escapeHtml(record.reason) : '') + '</small></article>';
        }).join('') + '</div>' : '<div class="code-tab-empty compact">No approval actions recorded.</div>';
        var terminal = approvalState === 'discarded' || approvalState === 'applied';
        return '<div class="code-pane-title"><div><span>Approval</span><b>Human-controlled Git lifecycle</b></div><span class="approval-state ' + escapeHtml(approvalState) + '">' + escapeHtml(approvalState) + '</span></div>' +
            '<div class="approval-separation"><div><b>1. Approve changes</b><span>Records the exact diff hash. No Git mutation.</span></div><div><b>2. Apply to branch</b><span>Creates a commit only after fresh verification.</span></div></div>' +
            '<div class="approval-actions"><button data-approval-action="revision" class="og-secondary-button"' + (terminal ? ' disabled' : '') + '>Request revision</button>' +
            '<button data-approval-action="approve" class="og-primary-button"' + (terminal ? ' disabled' : '') + '>Approve changes</button>' +
            '<button data-approval-action="apply" class="og-primary-button"' + (approvalState !== 'approved' ? ' disabled' : '') + '>Apply to branch</button>' +
            '<button data-approval-action="export" class="og-secondary-button">Export patch</button>' +
            '<button data-approval-action="discard" class="approval-danger"' + (terminal ? ' disabled' : '') + '>Discard</button></div>' +
            (task.appliedCommit ? '<div class="applied-commit"><span>Applied commit</span><code>' + escapeHtml(task.appliedCommit) + '</code></div>' : '') + history;
    }

    function renderResultTab(tab) {
        if (!state.task) return;
        document.querySelectorAll('[data-code-tab]').forEach(function (button) { button.classList.toggle('active', button.getAttribute('data-code-tab') === tab); });
        var target = document.getElementById('code-tab-content');
        if (tab === 'summary') target.innerHTML = summaryTab();
        else if (tab === 'changes') target.innerHTML = changesTab();
        else if (tab === 'diff') target.innerHTML = diffTab();
        else if (tab === 'verification') target.innerHTML = verificationTab();
        else if (tab === 'review') target.innerHTML = reviewTab();
        else target.innerHTML = '<div class="code-tab-empty"><b>Task Artifacts</b><span>Verification output uses immutable output references. Workflow Artifacts remain available in Projects.</span></div>';
        target.querySelectorAll('[data-run-command]').forEach(function (button) {
            button.addEventListener('click', function () { runVerification(button); });
        });
        target.querySelectorAll('[data-approval-action]').forEach(function (button) {
            button.addEventListener('click', function () { performApprovalAction(button.getAttribute('data-approval-action')); });
        });
    }

    function actorPayload(reason) {
        return {actorId: 'local-user', actorType: 'human', reason: reason || ''};
    }

    async function performApprovalAction(action) {
        var reason = '';
        try {
            if (action === 'revision') {
                reason = window.prompt('Describe the required revision:', '') || '';
                if (!reason) return;
                await api.requestRevision(state.project.id, state.task.id, actorPayload(reason));
            } else if (action === 'approve') {
                if (!window.confirm('Approve the exact current diff hash? This does not modify Git.')) return;
                await api.approveChanges(state.project.id, state.task.id, actorPayload('Approved in Code Workspace'));
            } else if (action === 'apply') {
                if (!window.confirm('Create a commit on the isolated task branch? The base branch will not be merged.')) return;
                var payload = actorPayload('Applied after explicit approval');
                payload.commitMessage = 'Apply approved Code Task changes';
                await api.applyChanges(state.project.id, state.task.id, payload);
            } else if (action === 'export') {
                var exported = await api.exportPatch(state.project.id, state.task.id, actorPayload('Manual patch export'));
                var patch = await api.getRecoveryPatch(state.project.id, state.task.id, exported.patch.id);
                downloadPatch(patch.patch);
            } else if (action === 'discard') {
                if (!window.confirm('Discard this isolated worktree? A recovery patch will be created first.')) return;
                var discard = actorPayload('Discarded in Code Workspace');
                discard.confirmation = 'DISCARD';
                await api.discardCodeTask(state.project.id, state.task.id, discard);
            }
            await refreshCurrentTask();
            renderResultTab('review');
        } catch (error) { setMessage(error.message, 'error'); }
    }

    function downloadPatch(patch) {
        var blob = new Blob([patch.content], {type: 'text/x-diff'});
        var link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = 'oxygent-' + patch.taskId + '.patch';
        link.click();
        URL.revokeObjectURL(link.href);
    }

    async function refreshCurrentTask() {
        var result = await api.getCodeTask(state.project.id, state.task.id);
        state.task = result.task;
        state.tasks = state.tasks.map(function (item) { return item.id === state.task.id ? state.task : item; });
        state.approvals = (await api.listApprovals(state.project.id, state.task.id)).items || [];
    }

    async function runVerification(button) {
        button.disabled = true;
        button.textContent = 'Running…';
        try {
            await api.runVerification(state.project.id, state.task.id, button.getAttribute('data-run-profile'), button.getAttribute('data-run-command'));
            state.runs = (await api.listVerificationRuns(state.project.id, state.task.id)).items || [];
            renderResultTab('verification');
        } catch (error) {
            setMessage(error.message, 'error');
            button.disabled = false;
            button.textContent = 'Run';
        }
    }

    async function loadTask(taskId) {
        state.task = state.tasks.find(function (item) { return item.id === taskId; }) || null;
        if (!state.task) return renderEmpty();
        renderTaskShell(state.task);
        if (state.task.approvalState === 'discarded') {
            state.approvals = (await api.listApprovals(state.project.id, state.task.id)).items || [];
            document.getElementById('repository-tree').innerHTML = '<span class="code-loading">Worktree discarded. Recovery patch remains available.</span>';
            renderResultTab('review');
            return;
        }
        try {
            var repository = state.repositories.find(function (item) { return item.id === state.task.repositoryId; });
            document.getElementById('repository-name').textContent = repository ? repository.name : 'Repository';
            var responses = await Promise.all([
                api.getRepositoryMetadata(state.project.id, state.task.id),
                api.getRepositoryTree(state.project.id, state.task.id, '.'),
                api.getCodeTaskDiff(state.project.id, state.task.id),
                api.listVerificationProfiles(state.project.id),
                api.listVerificationRuns(state.project.id, state.task.id),
                api.listApprovals(state.project.id, state.task.id)
            ]);
            var metadata = responses[0].result.data;
            state.diff = responses[2].diff;
            state.profiles = responses[3].items || [];
            state.runs = responses[4].items || [];
            state.approvals = responses[5].items || [];
            document.querySelector('#repository-metadata [data-clean]');
            renderFiles(responses[1].result.data.files || []);
            renderResultTab(state.runs.length ? 'verification' : 'summary');
            if (!metadata.clean) setMessage('The isolated worktree contains local changes. The source workspace remains untouched.', 'warning');
        } catch (error) {
            setMessage(error.message, 'error');
            document.getElementById('repository-tree').innerHTML = '<span class="code-loading">Repository context unavailable.</span>';
        }
    }

    function renderFiles(files, search) {
        var target = document.getElementById('repository-tree');
        document.getElementById('tree-count').textContent = files.length + (search ? ' matches' : ' files');
        target.innerHTML = files.length ? files.slice(0, 200).map(function (item) {
            var path = search ? item.split(':', 1)[0] : item;
            return '<button class="code-file" data-file="' + escapeHtml(path) + '"><span>◇</span><b>' + escapeHtml(item) + '</b></button>';
        }).join('') : '<span class="code-loading">No files found in the allowed scope.</span>';
        target.querySelectorAll('[data-file]').forEach(function (button) {
            button.addEventListener('click', function () { previewFile(button.getAttribute('data-file')); });
        });
    }

    async function searchRepository(event) {
        event.preventDefault();
        var query = new FormData(event.currentTarget).get('query').trim();
        if (!query) return;
        try {
            var data = await api.searchRepository(state.project.id, state.task.id, query, '.');
            renderFiles(data.result.data.matches || [], true);
        } catch (error) { setMessage(error.message, 'error'); }
    }

    async function previewFile(path) {
        try {
            var data = await api.readRepositoryFile(state.project.id, state.task.id, path);
            var preview = document.getElementById('file-preview');
            preview.hidden = false;
            document.getElementById('preview-path').textContent = path;
            document.getElementById('preview-content').textContent = data.result.data.content;
            document.getElementById('close-preview').onclick = function () { preview.hidden = true; };
        } catch (error) { setMessage(error.message, 'error'); }
    }

    async function loadProject(projectId) {
        state.project = state.projects.find(function (item) { return item.id === projectId; }) || null;
        if (!state.project) return renderEmpty();
        var responses = await Promise.all([api.listRepositories(projectId), api.listCodeTasks(projectId)]);
        state.repositories = responses[0].items || [];
        state.tasks = responses[1].items || [];
        var taskSelect = document.getElementById('code-task-select');
        taskSelect.innerHTML = options(state.tasks, function (item) { return item.id; }, function (item) { return item.branch; }, 'No Code Tasks');
        taskSelect.disabled = state.tasks.length === 0;
        await loadTask(taskSelect.value);
    }

    function openModal(id) {
        var dialog = document.getElementById(id);
        dialog.hidden = false;
        if (id === 'repository-modal') {
            dialog.querySelector('[name="projectId"]').innerHTML = options(state.projects, function (item) { return item.id; }, function (item) { return item.name; }, 'No Projects');
            dialog.querySelector('[name="rootReference"]').innerHTML = options(state.sources, function (item) { return item.reference; }, function (item) { return item.name; }, 'No approved sources');
        } else {
            var repositorySelect = dialog.querySelector('[name="repositoryId"]');
            repositorySelect.innerHTML = options(state.repositories, function (item) { return item.id; }, function (item) { return item.name; }, 'Register a repository first');
            function branches() {
                var repository = state.repositories.find(function (item) { return item.id === repositorySelect.value; });
                dialog.querySelector('[name="baseBranch"]').innerHTML = options((repository && repository.allowedBaseBranches || []).map(function (name) { return {name: name}; }), function (item) { return item.name; }, function (item) { return item.name; }, 'No base branches');
            }
            repositorySelect.onchange = branches;
            branches();
        }
    }

    async function submitRepository(event) {
        event.preventDefault();
        var form = event.currentTarget;
        var data = new FormData(form);
        try {
            await api.registerRepository(data.get('projectId'), {
                name: data.get('name').trim(), rootReference: data.get('rootReference'), defaultBranch: data.get('defaultBranch').trim(),
                allowedBaseBranches: data.get('allowedBranches').split(',').map(function (item) { return item.trim(); }).filter(Boolean)
            });
            document.getElementById('repository-modal').hidden = true;
            await loadProject(state.project.id);
        } catch (error) { form.querySelector('[data-modal-error]').textContent = error.message; }
    }

    async function submitTask(event) {
        event.preventDefault();
        var form = event.currentTarget;
        var data = new FormData(form);
        try {
            await api.createCodeTask(state.project.id, {
                repositoryId: data.get('repositoryId'), baseBranch: data.get('baseBranch'),
                changeContract: {
                    objective: data.get('objective').trim(), acceptanceCriteria: data.get('criteria').split('\n').map(function (item) { return item.trim(); }).filter(Boolean),
                    allowedPaths: data.get('allowedPaths').split(',').map(function (item) { return item.trim(); }).filter(Boolean),
                    forbiddenPaths: data.get('forbiddenPaths').split(',').map(function (item) { return item.trim(); }).filter(Boolean),
                    maxChangedFiles: Number(data.get('maxChangedFiles')), maxDiffLines: Number(data.get('maxDiffLines')),
                    dependencyChangesAllowed: data.get('dependencyChangesAllowed') === 'on', risk: 'medium'
                }
            });
            document.getElementById('task-modal').hidden = true;
            await loadProject(state.project.id);
        } catch (error) { form.querySelector('[data-modal-error]').textContent = error.message; }
    }

    async function mount() {
        var root = document.getElementById('code-workspace-page');
        if (!root || !window.OxyGentApp || !window.OxyGentApp.api) return;
        api = window.OxyGentApp.api;
        root.innerHTML = shell();
        document.querySelectorAll('[data-close-modal]').forEach(function (button) {
            button.addEventListener('click', function () { document.getElementById(button.getAttribute('data-close-modal')).hidden = true; });
        });
        document.getElementById('register-repository').addEventListener('click', function () { openModal('repository-modal'); });
        document.getElementById('new-code-task').addEventListener('click', function () { openModal('task-modal'); });
        document.querySelector('[data-modal-form="repository-modal"]').addEventListener('submit', submitRepository);
        document.querySelector('[data-modal-form="task-modal"]').addEventListener('submit', submitTask);
        document.getElementById('code-project-select').addEventListener('change', function (event) { loadProject(event.target.value); });
        document.getElementById('code-task-select').addEventListener('change', function (event) { loadTask(event.target.value); });
        try {
            var responses = await Promise.all([api.capabilities(), api.listProjects(), api.listRepositorySources()]);
            if (!responses[0].capabilities.codeWorkspace) throw new Error('Code Workspace is disabled until repository roots and a worktree root are configured.');
            state.projects = responses[1].items || [];
            state.sources = responses[2].items || [];
            var projectSelect = document.getElementById('code-project-select');
            projectSelect.innerHTML = options(state.projects, function (item) { return item.id; }, function (item) { return item.name; }, 'Create a Project first');
            projectSelect.disabled = state.projects.length === 0;
            await loadProject(projectSelect.value);
        } catch (error) {
            setMessage(error.message, 'warning');
            document.getElementById('register-repository').disabled = true;
            document.getElementById('new-code-task').disabled = true;
            renderEmpty();
        }
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
})();
