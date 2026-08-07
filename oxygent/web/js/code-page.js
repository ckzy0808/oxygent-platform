(function () {
    'use strict';

    var api;
    var root;
    var pollTimer = null;
    var state = {
        projects: [], project: null, sources: [], source: null,
        artifacts: [], workflowRuns: [], codeRuns: [], run: null,
        usageSummary: {}, usageItems: [],
        changeSet: null, selectedPath: '', fileChange: null,
        previewMode: 'after', previewLoading: false,
        lifecycle: {verification: null, verificationRuns: [], review: null, approval: null},
        lifecycleBusy: ''
    };
    var artifactLabels = {
        RequirementSpec: '需求规格', ArchitectureDecision: '架构设计',
        TaskGraph: '实现计划', ReviewReport: '方案审查'
    };
    var statusLabels = {
        queued: '排队中', running: '实现中', completed: '已完成', failed: '失败',
        valid: '有效', unvalidated: '未验证', invalid: '无效'
    };

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (character) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character];
        });
    }

    function formatBytes(value) {
        var bytes = Number(value || 0);
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    }

    function formatDate(value) {
        return value ? new Date(value).toLocaleString('zh-CN') : '—';
    }

    function setMessage(message, kind) {
        var target = document.getElementById('code-message');
        if (!target) return;
        target.hidden = !message;
        target.className = 'code-message ' + (kind || 'info');
        target.textContent = message || '';
    }

    function options(items, selected, value, label, empty) {
        if (!items.length) return '<option value="">' + escapeHtml(empty) + '</option>';
        return items.map(function (item) {
            var itemValue = value(item);
            return '<option value="' + escapeHtml(itemValue) + '"' + (itemValue === selected ? ' selected' : '') + '>' + escapeHtml(label(item)) + '</option>';
        }).join('');
    }

    function artifactSummary(artifact) {
        var content = artifact.content || {};
        return content.summary || '已生成结构化产物';
    }

    function phaseState(type) {
        return state.artifacts.some(function (item) { return item.type === type; }) ? 'complete' : 'pending';
    }

    function shell() {
        return '<header class="og-workspace-header code-header"><div><p class="og-workspace-eyebrow">项目工作流 · 代码实现阶段</p>' +
            '<h1 class="og-workspace-title">代码实现</h1><p class="og-header-detail">Aider 自动读取需求、架构、任务计划和已上传项目，并输出真实代码文件。</p></div>' +
            '<div class="code-header-actions"><input id="project-folder-input" type="file" webkitdirectory directory multiple hidden>' +
            '<button class="og-secondary-button" id="upload-project-folder">上传已有项目</button><button class="og-secondary-button" id="create-empty-source">新建空白项目</button></div></header>' +
            '<div class="og-workspace-content code-stage-content"><div class="code-toolbar code-stage-toolbar"><label>项目<select id="code-project-select"></select></label>' +
            '<label>项目源码<select id="code-source-select"></select></label><span class="code-stage-privacy">上传的是受管副本，不会修改电脑中的原文件夹</span></div>' +
            '<div id="code-message" class="code-message" hidden></div><div id="code-stage-body"></div></div>';
    }

    function renderNoProject() {
        document.getElementById('code-stage-body').innerHTML = '<section class="code-empty"><div class="code-empty-icon">⌘</div><h2>先创建一个项目</h2>' +
            '<p>代码实现属于项目工作流。请先在“项目”页面创建项目并填写 Idea，完成需求、架构和计划后再回到这里。</p>' +
            '<a class="og-primary-button code-link-button" href="projects.html">前往项目</a></section>';
    }

    function renderPhases() {
        var implementation = state.run ? (state.run.status === 'completed' ? 'complete' : state.run.status === 'failed' ? 'failed' : 'active') : 'active';
        var lifecycle = state.lifecycle || {};
        var verification = lifecycle.verification && lifecycle.verification.status === 'passed' ? 'complete' :
            (lifecycle.verification && lifecycle.verification.status === 'failed' ? 'failed' : (state.lifecycleBusy === 'verify' ? 'active' : 'pending'));
        var review = lifecycle.review && (lifecycle.review.status === 'approved' || lifecycle.review.humanOverride) ? 'complete' :
            lifecycle.review && lifecycle.review.status === 'basicallyQualified' ? 'warning' :
                lifecycle.review && lifecycle.review.status === 'changesRequested' ? 'failed' :
                    state.lifecycleBusy === 'review' ? 'active' : 'pending';
        var approval = lifecycle.approval && lifecycle.approval.status === 'approved' ? 'complete' : (state.lifecycleBusy === 'approve' ? 'active' : 'pending');
        var phases = [
            ['需求', phaseState('RequirementSpec')],
            ['架构', phaseState('ArchitectureDecision')],
            ['计划', phaseState('TaskGraph')],
            ['代码实现', implementation],
            ['验证', verification], ['审查', review], ['审批', approval]
        ];
        return '<ol class="code-stage-phases">' + phases.map(function (item) {
            return '<li class="' + item[1] + '"><span></span><b>' + item[0] + '</b><small>' +
                (item[1] === 'complete' ? '已完成' : item[1] === 'warning' ? '基本合格' : item[1] === 'active' ? '当前阶段' : item[1] === 'failed' ? '需处理' : '待后续') + '</small></li>';
        }).join('') + '</ol>';
    }

    function renderContext() {
        var source = state.source;
        var sourceCard = source ? '<section class="code-stage-card source-summary"><div class="code-stage-card-title"><span>' + (source.fileCount ? '已导入项目' : '从零创建') + '</span><b>' + escapeHtml(source.name) + '</b></div>' +
            '<dl><div><dt>文件</dt><dd>' + source.fileCount + ' 个</dd></div><div><dt>大小</dt><dd>' + formatBytes(source.totalBytes) + '</dd></div><div><dt>导入时间</dt><dd>' + escapeHtml(formatDate(source.createdAt)) + '</dd></div></dl>' +
            (source.fileCount ? '<p class="code-source-mode-note">Aider 将读取这些文件并进行修改。</p>' : '<p class="code-source-mode-note new">这是新项目。Aider 将根据前序产物从零创建完整文件，不需要现有仓库。</p>') +
            '<button class="og-secondary-button" id="replace-project-folder">改为上传已有项目</button></section>' :
            '<section class="code-stage-card source-empty"><b>请选择代码来源</b><p>修改现有代码请选择“上传已有项目”；从零开发请选择“新建空白项目”。</p>' +
            '<button class="og-primary-button" id="inline-upload-folder">上传已有项目</button><button class="og-secondary-button" id="inline-empty-source">新建空白项目</button></section>';
        var artifactCards = state.artifacts.length ? state.artifacts.slice().reverse().map(function (artifact) {
            return '<article class="code-artifact-context"><div><span>' + escapeHtml(artifactLabels[artifact.type] || artifact.type) + '</span><small>' + escapeHtml(statusLabels[artifact.validationStatus] || artifact.validationStatus) + '</small></div>' +
                '<p>' + escapeHtml(artifactSummary(artifact)) + '</p></article>';
        }).join('') : '<div class="code-stage-empty-note">暂无前序产物。可以直接描述改动，但先运行项目工作流能获得更准确的实现。</div>';
        return '<aside class="code-stage-column context-column"><h2>实现上下文</h2>' + sourceCard + '<section class="code-stage-card"><div class="code-stage-card-title"><span>自动传给 Aider</span><b>前序工作流产物</b></div><div class="code-artifact-list">' + artifactCards + '</div></section></aside>';
    }

    function suggestedInstructions() {
        var graph = state.artifacts.slice().reverse().find(function (item) { return item.type === 'TaskGraph'; });
        if (!graph || !graph.content) return '';
        var tasks = graph.content.tasks || [];
        return tasks.map(function (task) { return task.title + (task.description ? '：' + task.description : ''); }).join('\n');
    }

    function renderImplementation() {
        var disabled = !state.source || (state.run && (state.run.status === 'running' || state.run.status === 'queued'));
        var suggestion = suggestedInstructions();
        return '<main class="code-stage-column implementation-column"><div class="code-stage-card-title"><span>告诉代码阶段要做什么</span><b>Aider 代码实现</b></div>' +
            '<form id="code-implementation-form"><label>改动目标<textarea name="instructions" rows="12" maxlength="20000" required placeholder="例如：根据需求和架构实现用户登录接口，并补充对应测试。">' + escapeHtml(suggestion) + '</textarea></label>' +
            '<div class="implementation-guidance"><span>✓ 自动读取上传的完整项目</span><span>✓ 自动读取需求、架构、计划和审查产物</span><span>✓ 生成真实文件供预览和下载</span></div>' +
            '<button class="og-primary-button code-run-button" type="submit"' + (disabled ? ' disabled' : '') + '>' +
            (state.run && (state.run.status === 'running' || state.run.status === 'queued') ? 'Aider 正在实现…' : '开始代码实现') + '</button>' +
            (!state.source ? '<p class="code-form-help">请先上传已有项目或新建空白项目。</p>' : (state.source.fileCount === 0 ? '<p class="code-form-help">当前为从零创建模式，Aider 会直接创建所需项目文件。</p>' : '')) + '</form></main>';
    }

    function renderResult() {
        var run = state.run;
        if (!run) return '<aside class="code-stage-column result-column"><div class="code-stage-card-title"><span>执行结果</span><b>等待开始</b></div>' +
            '<div class="code-stage-result-empty"><div>⌁</div><p>代码生成完成后，这里会直接列出新增或修改的文件。无需理解 Git、分支或 Diff。</p></div>' + historyMarkup() + '</aside>';
        if (run.status === 'queued' || run.status === 'running') return '<aside class="code-stage-column result-column"><div class="code-stage-card-title"><span>执行结果</span><b>' + escapeHtml(statusLabels[run.status]) + '</b></div>' +
            '<div class="code-stage-running"><span class="code-stage-spinner"></span><h3>Aider 正在实现代码</h3><p>' + escapeHtml(run.summary || '正在准备隔离项目副本，尚未调用模型。') + '</p>' + usageMarkup() + '</div>' + historyMarkup() + '</aside>';
        if (run.status === 'failed') return '<aside class="code-stage-column result-column"><div class="code-stage-card-title"><span>执行结果</span><b class="code-stage-failed">实现失败</b></div>' +
            '<div class="code-stage-error"><b>可操作的错误信息</b><p>' + escapeHtml(run.failureReason || 'Aider 未能完成实现。') + '</p><button class="og-secondary-button" id="retry-code-run">修改说明后重试</button></div>' + historyMarkup() + '</aside>';
        var files = run.changedFiles || [];
        return '<aside class="code-stage-column result-column"><div class="code-stage-card-title"><span>执行结果</span><b class="code-stage-success">已生成代码</b></div>' +
            '<section class="code-result-summary"><p>' + escapeHtml(run.summary) + '</p><small>模型：' + escapeHtml(run.modelId || '—') + '</small>' + usageMarkup() + '</section>' +
            '<div class="generated-file-list"><h3>生成或修改的文件</h3>' + files.map(function (path) {
                return '<button type="button" data-preview-file="' + escapeHtml(path) + '" class="' + (state.selectedPath === path ? 'active' : '') + '"><span>文件</span><b>' + escapeHtml(path) + '</b><small>查看代码</small></button>';
            }).join('') + '</div><form id="code-revision-form" class="code-revision-form"><label>对本轮结果不满意？直接提出修改要求<textarea name="feedback" rows="4" maxlength="20000" required placeholder="例如：登录页不要使用弹窗；改成独立页面，并为错误状态补充测试。"></textarea></label>' +
            '<button class="og-secondary-button" type="submit">按反馈继续修改</button><small>新一轮会以当前代码为基础，不会回到最初上传版本。</small></form>' +
            '<p class="code-result-gate-note">代码确认无误后，请在下方依次完成真实验证、Reviewer 审查和人工审批。审批后才提供最终项目下载。</p>' + historyMarkup() + '</aside>';
    }

    function usageMarkup() {
        var usage = state.usageSummary || {};
        var total = Number(usage.totalTokens || 0);
        var exact = Number(usage.exactInvocations || 0);
        var estimated = Number(usage.estimatedInvocations || 0);
        var active = Number(usage.activeInvocations || 0);
        var waiting = state.run && (state.run.status === 'queued' || state.run.status === 'running') && !active && !exact && !estimated;
        return '<div class="code-token-usage"><b>' + (waiting ? '尚未调用模型' : total.toLocaleString('zh-CN') + ' Token') + '</b><span>' + (waiting ? 'Aider 正在准备项目上下文' : '输入 ' + Number(usage.inputTokens || 0).toLocaleString('zh-CN') + ' · 输出 ' + Number(usage.outputTokens || 0).toLocaleString('zh-CN')) + '</span><small>' + (active ? active + ' 次 API 调用中 · ' : '') + exact + ' 次 API 精确计量' + (estimated ? ' · ' + estimated + ' 次估算' : '') + '</small></div>';
    }

    async function loadRunUsage() {
        if (!state.run || !state.project) {
            state.usageSummary = {};
            state.usageItems = [];
            return;
        }
        var result = await api.listUsage({projectId: state.project.id, runId: state.run.id});
        state.usageSummary = result.summary || {};
        state.usageItems = result.items || [];
    }

    function renderCodeReview() {
        var run = state.run;
        if (!run || run.status !== 'completed') return '';
        var changes = state.changeSet;
        var paths = changes ? (changes.changedFiles || []) : (run.changedFiles || []);
        var metrics = changes ? '<div class="code-review-metrics"><span>+' + Number(changes.additions || 0) + '</span><span>−' + Number(changes.deletions || 0) + '</span><span>' + paths.length + ' 个文件</span>' + (changes.truncated ? '<em>Diff 已截断</em>' : '') + '</div>' : '';
        var fileList = paths.map(function (path) {
            return '<button type="button" data-preview-file="' + escapeHtml(path) + '" class="' + (state.selectedPath === path ? 'active' : '') + '"><span>◻</span><b>' + escapeHtml(path) + '</b></button>';
        }).join('');
        var tabs = '<div class="code-preview-tabs"><button type="button" data-preview-mode="after" class="' + (state.previewMode === 'after' ? 'active' : '') + '">修改后代码</button>' +
            '<button type="button" data-preview-mode="before" class="' + (state.previewMode === 'before' ? 'active' : '') + '">修改前代码</button>' +
            '<button type="button" data-preview-mode="diff" class="' + (state.previewMode === 'diff' ? 'active' : '') + '">本轮 Diff</button></div>';
        var content = '';
        if (state.previewLoading) {
            content = '<div class="code-preview-placeholder">正在读取代码…</div>';
        } else if (state.previewMode === 'diff') {
            content = '<pre class="code-live-preview diff">' + escapeHtml(changes && changes.diff || '本轮没有可显示的文本 Diff。') + '</pre>';
        } else if (!state.fileChange) {
            content = '<div class="code-preview-placeholder">选择左侧文件查看代码。</div>';
        } else if (state.fileChange.binary) {
            content = '<div class="code-preview-placeholder">该文件是二进制文件，无法在网页中显示。</div>';
        } else {
            var value = state.previewMode === 'before' ? state.fileChange.beforeContent : state.fileChange.afterContent;
            var emptyLabel = state.previewMode === 'before' && state.fileChange.changeType === 'added' ? '这是本轮新增文件，修改前不存在。' :
                (state.previewMode === 'after' && state.fileChange.changeType === 'deleted' ? '该文件已在本轮删除。' : '文件内容为空。');
            content = value ? '<pre class="code-live-preview"><code>' + escapeHtml(value) + '</code></pre>' : '<div class="code-preview-placeholder">' + emptyLabel + '</div>';
        }
        var changeType = state.fileChange ? ({added: '新增', modified: '修改', deleted: '删除'}[state.fileChange.changeType] || state.fileChange.changeType) : '';
        return '<section class="code-review-workspace"><header><div><span>代码审阅</span><h2>查看 Aider 生成和修改的真实代码</h2></div>' + metrics + '</header><div class="code-review-layout"><aside><h3>本轮变更</h3><div class="code-review-files">' + fileList + '</div></aside>' +
            '<main><div class="code-preview-header"><div><b>' + escapeHtml(state.selectedPath || '请选择文件') + '</b>' + (changeType ? '<span class="' + escapeHtml(state.fileChange.changeType) + '">' + escapeHtml(changeType) + '</span>' : '') + '</div>' + tabs + '</div>' + content + '</main></div></section>';
    }

    function lifecycleStatusLabel(value) {
        return ({notStarted: '未开始', running: '执行中', passed: '已通过', failed: '未通过',
            approved: '完全通过', basicallyQualified: '基本合格', changesRequested: '需要修改', pending: '待审批'}[value] || value || '未开始');
    }

    function renderLifecycle() {
        if (!state.run || state.run.status !== 'completed') return '';
        var lifecycle = state.lifecycle || {};
        var verification = lifecycle.verification;
        var verificationRuns = lifecycle.verificationRuns || [];
        var review = lifecycle.review;
        var approval = lifecycle.approval;
        var verifying = state.lifecycleBusy === 'verify';
        var reviewing = state.lifecycleBusy === 'review';
        var approving = state.lifecycleBusy === 'approve';
        var verificationPassed = verification && verification.status === 'passed';
        var reviewApproved = review && (review.status === 'approved' || review.status === 'basicallyQualified' || review.humanOverride);

        var verificationDetails = verificationRuns.length ? '<div class="code-lifecycle-runs">' + verificationRuns.map(function (item) {
            var argv = item.argv || [];
            var status = item.status || 'failed';
            return '<article class="' + escapeHtml(status) + '"><header><b>' + escapeHtml(item.commandName || item.commandId) + '</b><span>' +
                escapeHtml(lifecycleStatusLabel(status)) + '</span></header><code>' + escapeHtml(argv.join(' ')) + '</code><p>真实退出码：<b>' +
                (item.exitCode == null ? '—' : escapeHtml(item.exitCode)) + '</b> · 用时 ' + escapeHtml(item.durationMs || 0) + ' ms' +
                (item.attemptCount > 1 ? ' · 已自动重试 ' + escapeHtml(item.attemptCount - 1) + ' 次' : '') + '</p>' +
                ((item.stdoutPreview || item.stderrPreview) ? '<details><summary>查看命令输出</summary><pre>' + escapeHtml((item.stdoutPreview || '') + (item.stderrPreview ? '\n' + item.stderrPreview : '')) + '</pre></details>' : '') + '</article>';
        }).join('') + '</div>' : '<p class="code-lifecycle-empty">尚未执行。系统会在隔离副本中根据项目类型运行固定参数的语法、编译或测试命令。</p>';
        var failedVerificationRuns = verificationRuns.filter(function (item) { return item.status !== 'passed'; });
        var onlyInfrastructureFailure = failedVerificationRuns.length && failedVerificationRuns.every(function (item) { return item.failureCategory === 'infrastructure'; });
        var verificationFailureAction = verification && verification.status === 'failed' ?
            (onlyInfrastructureFailure ? '<div class="code-verification-fix"><b>验证环境暂时不可用</b><p>检测到依赖下载、网络或本机工具错误，代码本身尚未被判定失败。请直接重新运行验证。</p></div>' :
            '<div class="code-verification-fix"><b>验证发现代码问题</b><p>可以把失败命令、退出码和错误输出直接交回 Aider 修复。</p>' +
            '<button class="og-primary-button" id="apply-verification-revision">一键根据验证错误修复</button></div>') : '';

        var findings = review && review.findings ? review.findings.map(function (item) {
            return '<li><span>' + escapeHtml(item.severity) + '</span>' + escapeHtml(item.message) + '</li>';
        }).join('') : '';
        var required = review && review.requiredChanges ? review.requiredChanges.map(function (item) {
            return '<li>' + escapeHtml(item) + '</li>';
        }).join('') : '';
        var overrideNotice = review && review.humanOverride ? '<div class="code-review-override-notice"><b>人工确认无需修改</b><p>' + escapeHtml(review.overrideReason) + '</p><small>' + escapeHtml(review.overrideActorId) + ' · ' + escapeHtml(formatDate(review.overriddenAt)) + '</small></div>' : '';
        var reviewDecisionActions = review && review.status === 'changesRequested' && !review.humanOverride ?
            '<div class="code-review-decision-actions"><button class="og-primary-button" id="apply-review-revision">一键按审查意见返回修改</button>' +
            '<form id="review-override-form"><b>人工判断无需修改？</b><label>操作人<input name="actorId" maxlength="160" value="local-user" required></label>' +
            '<label>跳过理由<textarea name="reason" rows="3" maxlength="2000" required placeholder="说明为什么 Reviewer 的意见不影响当前交付"></textarea></label>' +
            '<button class="og-secondary-button" type="submit">无需修改，进入最终审批</button><small>此操作会保留 Reviewer 原意见并写入人工审计记录。</small></form></div>' : '';
        var basicReviewAction = review && review.status === 'basicallyQualified' ?
            '<div class="code-review-basic-action"><b>核心需求已满足</b><p>这些是非阻塞改进建议，你可以直接进入最终审批，也可以让 Aider 继续优化。</p>' +
            (review.findings && review.findings.length ? '<button class="og-secondary-button" id="apply-review-revision">一键按建议继续优化</button>' : '') + '</div>' : '';
        var reviewDetails = review ? '<div class="code-review-report"><p>' + escapeHtml(review.summary || 'Reviewer 已完成审查。') + '</p>' +
            (findings ? '<h4>发现</h4><ul class="code-review-findings">' + findings + '</ul>' : '') +
            (required ? '<h4>必须修改</h4><ol>' + required + '</ol>' : '') +
            '<small>Reviewer：' + escapeHtml(review.providerId || '—') + ' / ' + escapeHtml(review.modelId || '—') + '</small></div>' + overrideNotice + basicReviewAction + reviewDecisionActions :
            '<p class="code-lifecycle-empty">验证通过后，Reviewer 会依据需求、架构、实现计划、真实命令结果和代码变更进行审查。</p>';

        var approvalDetails = approval ? '<div class="code-approval-success"><b>当前代码版本已人工批准</b><p>审批人：' + escapeHtml(approval.actorId) +
            ' · ' + escapeHtml(formatDate(approval.createdAt)) + '</p>' + (approval.reason ? '<p>' + escapeHtml(approval.reason) + '</p>' : '') +
            '<a class="og-primary-button code-download-project" href="' + escapeHtml(api.codeStageDownloadUrl(state.project.id, state.run.id)) + '">下载最终完整项目 ZIP</a></div>' :
            '<form id="code-approval-form" class="code-approval-form"><label>审批人<input name="actorId" maxlength="160" value="local-user" required></label>' +
            '<label>审批说明<textarea name="reason" rows="3" maxlength="4000" placeholder="说明为什么接受当前代码版本（可选）"></textarea></label>' +
            '<button class="og-primary-button" type="submit"' + (!reviewApproved || approving ? ' disabled' : '') + '>' + (approving ? '正在审批…' : '批准当前代码版本') + '</button>' +
            (!reviewApproved ? '<small>必须先通过 Reviewer 审查，或由人工说明理由后确认无需修改。</small>' : '<small>审批只确认当前内容哈希；后续代码变化会使审批失效。</small>') + '</form>';

        return '<section class="code-lifecycle"><header><div><span>质量门禁</span><h2>验证、审查与审批</h2></div><p>每一步都绑定当前代码内容；代码发生变化后必须重新执行。</p></header><div class="code-lifecycle-grid">' +
            '<article class="code-lifecycle-card"><div class="code-lifecycle-title"><span>1</span><div><small>Verification</small><h3>真实运行验证</h3></div><b class="status-' + escapeHtml(verification && verification.status || 'notStarted') + '">' + escapeHtml(lifecycleStatusLabel(verification && verification.status)) + '</b></div>' + verificationDetails + verificationFailureAction +
            '<button class="og-primary-button" id="run-code-verification"' + (verifying ? ' disabled' : '') + '>' + (verifying ? '正在验证…' : (verification ? '重新运行验证' : '开始验证')) + '</button></article>' +
            '<article class="code-lifecycle-card"><div class="code-lifecycle-title"><span>2</span><div><small>Reviewer</small><h3>模型独立审查</h3></div><b class="status-' + escapeHtml(review && review.status || 'notStarted') + '">' + escapeHtml(lifecycleStatusLabel(review && review.status)) + '</b></div>' + reviewDetails +
            '<button class="og-primary-button" id="run-code-review"' + (!verificationPassed || reviewing ? ' disabled' : '') + '>' + (reviewing ? '正在审查…' : (review ? '重新执行审查' : '开始审查')) + '</button></article>' +
            '<article class="code-lifecycle-card"><div class="code-lifecycle-title"><span>3</span><div><small>Human Approval</small><h3>最终人工审批</h3></div><b class="status-' + escapeHtml(approval && approval.status || 'pending') + '">' + escapeHtml(lifecycleStatusLabel(approval && approval.status || 'pending')) + '</b></div>' + approvalDetails + '</article></div></section>';
    }

    function historyMarkup() {
        if (!state.codeRuns.length) return '';
        return '<section class="code-run-history"><h3>最近实现</h3>' + state.codeRuns.slice(0, 5).map(function (run) {
            return '<button data-code-run="' + escapeHtml(run.id) + '" class="' + (state.run && state.run.id === run.id ? 'active' : '') + '"><span>' + escapeHtml(statusLabels[run.status] || run.status) + '</span><b>' + escapeHtml(run.instructions.slice(0, 60)) + '</b><small>' + escapeHtml(formatDate(run.createdAt)) + '</small></button>';
        }).join('') + '</section>';
    }

    function renderBody() {
        if (!state.project) return renderNoProject();
        document.getElementById('code-stage-body').innerHTML = renderPhases() + '<div class="code-stage-layout">' + renderContext() + renderImplementation() + renderResult() + '</div>' + renderCodeReview() + renderLifecycle();
        bindBodyActions();
    }

    function bindBodyActions() {
        ['replace-project-folder', 'inline-upload-folder'].forEach(function (id) {
            var button = document.getElementById(id);
            if (button) button.addEventListener('click', openFolderPicker);
        });
        var blank = document.getElementById('inline-empty-source');
        if (blank) blank.addEventListener('click', createBlankSource);
        var form = document.getElementById('code-implementation-form');
        if (form) form.addEventListener('submit', startImplementation);
        var revisionForm = document.getElementById('code-revision-form');
        if (revisionForm) revisionForm.addEventListener('submit', startRevision);
        var retry = document.getElementById('retry-code-run');
        if (retry) retry.addEventListener('click', function () {
            state.run = null;
            renderBody();
            var textarea = document.querySelector('[name="instructions"]');
            if (textarea) textarea.focus();
        });
        document.querySelectorAll('[data-code-run]').forEach(function (button) {
            button.addEventListener('click', async function () {
                await selectRun(state.codeRuns.find(function (item) { return item.id === button.getAttribute('data-code-run'); }) || null);
            });
        });
        document.querySelectorAll('[data-preview-file]').forEach(function (button) {
            button.addEventListener('click', function () { loadFileChange(button.getAttribute('data-preview-file')); });
        });
        document.querySelectorAll('[data-preview-mode]').forEach(function (button) {
            button.addEventListener('click', function () {
                state.previewMode = button.getAttribute('data-preview-mode');
                renderBody();
            });
        });
        var verify = document.getElementById('run-code-verification');
        if (verify) verify.addEventListener('click', runCodeVerification);
        var verificationRevision = document.getElementById('apply-verification-revision');
        if (verificationRevision) verificationRevision.addEventListener('click', reviseFromVerification);
        var review = document.getElementById('run-code-review');
        if (review) review.addEventListener('click', runCodeReview);
        var reviewRevision = document.getElementById('apply-review-revision');
        if (reviewRevision) reviewRevision.addEventListener('click', reviseFromReview);
        var reviewOverrideForm = document.getElementById('review-override-form');
        if (reviewOverrideForm) reviewOverrideForm.addEventListener('submit', overrideReviewDecision);
        var approvalForm = document.getElementById('code-approval-form');
        if (approvalForm) approvalForm.addEventListener('submit', approveCurrentCode);
    }

    function openFolderPicker() {
        if (!state.project) return setMessage('请先选择项目。', 'warning');
        document.getElementById('project-folder-input').click();
    }

    async function importFolder(event) {
        var files = event.target.files;
        if (!files || !files.length) return;
        var top = files[0].webkitRelativePath ? files[0].webkitRelativePath.split('/')[0] : '上传的项目';
        setMessage('正在检查并导入项目文件…', 'info');
        try {
            var result = await api.importSourceWorkspace(state.project.id, files, top);
            state.source = result.sourceWorkspace;
            await loadProjectData(state.project.id, state.source.id);
            var stats = result.importStats || {};
            setMessage('浏览器发现 ' + (stats.discoveredCount || state.source.selectedFileCount || state.source.fileCount) +
                ' 个文件，实际导入 ' + state.source.fileCount + ' 个项目文件' +
                (stats.skipped ? '，自动跳过 ' + stats.skipped + ' 个依赖、构建或敏感文件' : '') + '。原始文件夹不会被修改。', 'success');
        } catch (error) {
            setMessage(error.message, 'error');
        } finally {
            event.target.value = '';
        }
    }

    async function createBlankSource() {
        if (!state.project) return setMessage('请先选择项目。', 'warning');
        try {
            var result = await api.createBlankSourceWorkspace(state.project.id, state.project.name + ' - 空白源码');
            state.source = result.sourceWorkspace;
            await loadProjectData(state.project.id, state.source.id);
            setMessage('已进入从零创建模式；Aider 会根据工作流产物直接生成完整项目，不会再索要仓库文件。', 'success');
        } catch (error) { setMessage(error.message, 'error'); }
    }

    function resetCodePreview() {
        state.changeSet = null;
        state.selectedPath = '';
        state.fileChange = null;
        state.previewMode = 'after';
        state.previewLoading = false;
    }

    function resetLifecycle() {
        state.lifecycle = {verification: null, verificationRuns: [], review: null, approval: null};
        state.lifecycleBusy = '';
    }

    async function loadLifecycle() {
        if (!state.project || !state.run || state.run.status !== 'completed') {
            resetLifecycle();
            return;
        }
        var runId = state.run.id;
        var result = await api.getCodeStageLifecycle(state.project.id, runId);
        if (!state.run || state.run.id !== runId) return;
        state.lifecycle = {
            verification: result.verification || null,
            verificationRuns: result.verificationRuns || [],
            review: result.review || null,
            approval: result.approval || null
        };
    }

    async function runCodeVerification() {
        if (!state.project || !state.run || state.lifecycleBusy) return;
        state.lifecycleBusy = 'verify';
        setMessage('正在隔离副本中运行真实验证命令…', 'info');
        renderBody();
        try {
            var result = await api.verifyCodeStage(state.project.id, state.run.id);
            state.lifecycle.verification = result.verification;
            state.lifecycle.verificationRuns = result.verificationRuns || [];
            state.lifecycle.review = null;
            state.lifecycle.approval = null;
            setMessage(result.verification.status === 'passed' ? '全部验证命令已通过。' : '验证未通过，请查看真实退出码和命令输出。', result.verification.status === 'passed' ? 'success' : 'error');
        } catch (error) {
            setMessage('验证失败：' + error.message, 'error');
        } finally {
            state.lifecycleBusy = '';
            renderBody();
        }
    }

    function verificationRevisionInstructions(runs) {
        var failed = (runs || []).filter(function (item) { return item.status !== 'passed' && item.failureCategory !== 'infrastructure'; });
        if (!failed.length) failed = runs || [];
        var lines = [
            '真实验证未通过。请根据以下命令结果修复当前代码，然后补充或更新相应测试。',
            '不要仅删除测试、跳过检查或隐藏错误；应修复导致失败的实际问题。'
        ];
        failed.forEach(function (item, index) {
            lines.push('', '失败项 ' + (index + 1) + '：' + (item.commandName || item.commandId || '未知命令'));
            lines.push('命令：' + ((item.argv || []).join(' ') || '未提供'));
            lines.push('状态：' + (item.status || 'failed') + '；真实退出码：' + (item.exitCode == null ? '未知' : item.exitCode));
            if (item.failureReason) lines.push('失败原因：' + item.failureReason);
            if (item.stderrPreview) lines.push('stderr：\n' + String(item.stderrPreview).slice(-5000));
            if (item.stdoutPreview) lines.push('stdout：\n' + String(item.stdoutPreview).slice(-3000));
        });
        lines.push('', '完成修改后，确保上述验证命令能够真实返回退出码 0。');
        return lines.join('\n').slice(0, 20000);
    }

    async function reviseFromVerification() {
        var lifecycle = state.lifecycle || {};
        if (!lifecycle.verification || lifecycle.verification.status !== 'failed') return;
        var failed = (lifecycle.verificationRuns || []).filter(function (item) { return item.status !== 'passed'; });
        var actionable = failed.filter(function (item) { return item.failureCategory !== 'infrastructure'; });
        if (failed.length && !actionable.length) {
            setMessage('本次失败来自依赖下载、网络或本机工具环境，不是源码错误。请点击“重新运行验证”，无需让 Aider 修改代码。', 'warning');
            renderBody();
            return;
        }
        await createRevision(
            verificationRevisionInstructions(lifecycle.verificationRuns || []),
            '正在把验证错误交给 Aider 创建修订…',
            '已将失败命令、退出码和错误输出交给 Aider，正在基于当前代码自动修复。'
        );
    }

    async function runCodeReview() {
        if (!state.project || !state.run || state.lifecycleBusy) return;
        state.lifecycleBusy = 'review';
        setMessage('Reviewer 正在结合前序产物、真实验证结果和代码变更进行审查…', 'info');
        renderBody();
        try {
            var result = await api.reviewCodeStage(state.project.id, state.run.id);
            state.lifecycle.review = result.review;
            state.lifecycle.approval = null;
            await loadRunUsage();
            if (result.review.status === 'approved') {
                setMessage('Reviewer 完全通过，可以进入最终审批。', 'success');
            } else if (result.review.status === 'basicallyQualified') {
                setMessage('Reviewer 判定基本合格：可以直接审批，也可以按非阻塞建议继续优化。', 'warning');
            } else {
                setMessage('Reviewer 判定需要修改，请根据报告创建下一轮修订。', 'error');
            }
        } catch (error) {
            setMessage('审查失败：' + error.message, 'error');
        } finally {
            state.lifecycleBusy = '';
            renderBody();
        }
    }

    async function reviseFromReview() {
        var review = state.lifecycle && state.lifecycle.review;
        if (!review || ['changesRequested', 'basicallyQualified'].indexOf(review.status) < 0 || review.humanOverride || state.lifecycleBusy) return;
        var parentRun = state.run;
        state.lifecycleBusy = 'revision';
        setMessage('正在从服务器保存的 Reviewer 结果创建结构化修订合同…', 'info');
        renderBody();
        try {
            var result = await api.startCodeStageReviewRevision(state.project.id, parentRun.id);
            state.run = result.run;
            state.codeRuns.unshift(result.run);
            state.usageSummary = {};
            state.usageItems = [];
            resetCodePreview();
            resetLifecycle();
            renderBody();
            beginPolling();
            setMessage('Reviewer 的必须修改项已由服务器编号并完整交给 Aider，正在基于当前最终代码修改。', 'success');
        } catch (error) {
            state.lifecycleBusy = '';
            setMessage('无法创建审查修订：' + error.message, 'error');
            renderBody();
        }
    }

    async function overrideReviewDecision(event) {
        event.preventDefault();
        if (!state.project || !state.run || state.lifecycleBusy) return;
        var form = new FormData(event.currentTarget);
        state.lifecycleBusy = 'override';
        renderBody();
        try {
            var result = await api.overrideCodeStageReview(state.project.id, state.run.id, {
                actorId: String(form.get('actorId') || '').trim(),
                reason: String(form.get('reason') || '').trim()
            });
            state.lifecycle.review = result.review;
            setMessage('已保留 Reviewer 原意见并记录人工判断，现在可以进入最终审批。', 'success');
        } catch (error) {
            setMessage('无法进入下一步：' + error.message, 'error');
        } finally {
            state.lifecycleBusy = '';
            renderBody();
        }
    }

    async function approveCurrentCode(event) {
        event.preventDefault();
        if (!state.project || !state.run || state.lifecycleBusy) return;
        var form = new FormData(event.currentTarget);
        state.lifecycleBusy = 'approve';
        renderBody();
        try {
            var result = await api.approveCodeStage(state.project.id, state.run.id, {
                actorId: String(form.get('actorId') || '').trim(),
                reason: String(form.get('reason') || '').trim()
            });
            state.lifecycle.approval = result.approval;
            setMessage('当前代码版本已批准，现在可以下载最终完整项目。', 'success');
        } catch (error) {
            setMessage('审批失败：' + error.message, 'error');
        } finally {
            state.lifecycleBusy = '';
            renderBody();
        }
    }

    async function selectRun(run) {
        state.run = run;
        state.usageSummary = {};
        state.usageItems = [];
        resetCodePreview();
        resetLifecycle();
        if (run) await Promise.all([loadRunUsage(), loadLifecycle()]);
        renderBody();
        if (run && run.status === 'completed') await loadRunChanges();
        beginPolling();
    }

    async function loadRunChanges() {
        if (!state.run || state.run.status !== 'completed') return;
        var runId = state.run.id;
        try {
            var result = await api.getCodeStageChanges(state.project.id, runId);
            if (!state.run || state.run.id !== runId) return;
            state.changeSet = result.changes;
            var paths = state.changeSet.changedFiles || [];
            state.selectedPath = paths[0] || '';
            if (state.selectedPath) await loadFileChange(state.selectedPath, false);
            else renderBody();
        } catch (error) {
            setMessage('代码预览加载失败：' + error.message, 'error');
            renderBody();
        }
    }

    async function loadFileChange(path, shouldRenderLoading) {
        if (!state.run || !path) return;
        var runId = state.run.id;
        state.selectedPath = path;
        state.fileChange = null;
        state.previewLoading = true;
        if (shouldRenderLoading !== false) renderBody();
        try {
            var result = await api.getCodeStageFileChange(state.project.id, runId, path);
            if (!state.run || state.run.id !== runId || state.selectedPath !== path) return;
            state.fileChange = result.change;
        } catch (error) {
            setMessage('文件预览加载失败：' + error.message, 'error');
        } finally {
            if (state.run && state.run.id === runId && state.selectedPath === path) {
                state.previewLoading = false;
                renderBody();
            }
        }
    }

    async function startImplementation(event) {
        event.preventDefault();
        var instructions = new FormData(event.currentTarget).get('instructions').trim();
        if (!state.source || !instructions) return;
        var button = event.currentTarget.querySelector('[type="submit"]');
        button.disabled = true;
        button.textContent = '正在启动…';
        setMessage('', 'info');
        try {
            var workflow = state.workflowRuns[0] || null;
            var result = await api.startCodeStageRun(state.project.id, {
                sourceWorkspaceId: state.source.id,
                workflowRunId: workflow ? workflow.runId : null,
                instructions: instructions
            });
            state.run = result.run;
            state.codeRuns.unshift(result.run);
            state.usageSummary = {};
            state.usageItems = [];
            resetCodePreview();
            resetLifecycle();
            renderBody();
            beginPolling();
        } catch (error) {
            setMessage(error.message, 'error');
            button.disabled = false;
            button.textContent = '开始代码实现';
        }
    }

    async function startRevision(event) {
        event.preventDefault();
        if (!state.run || state.run.status !== 'completed') return;
        var feedback = new FormData(event.currentTarget).get('feedback').trim();
        if (!feedback) return;
        var button = event.currentTarget.querySelector('[type="submit"]');
        button.disabled = true;
        button.textContent = '正在创建修订…';
        await createRevision(feedback, '', '已基于上一轮代码开始修改，不会退回最初版本。', button);
    }

    async function createRevision(feedback, progressMessage, successMessage, sourceButton) {
        if (!state.run || state.run.status !== 'completed' || state.lifecycleBusy) return;
        var parentRun = state.run;
        state.lifecycleBusy = 'revision';
        if (progressMessage) setMessage(progressMessage, 'info');
        else setMessage('', 'info');
        if (!sourceButton) renderBody();
        try {
            var result = await api.startCodeStageRun(state.project.id, {
                sourceWorkspaceId: parentRun.sourceWorkspaceId,
                workflowRunId: parentRun.workflowRunId || null,
                parentRunId: parentRun.id,
                instructions: feedback
            });
            state.run = result.run;
            state.codeRuns.unshift(result.run);
            state.usageSummary = {};
            state.usageItems = [];
            resetCodePreview();
            resetLifecycle();
            renderBody();
            beginPolling();
            setMessage(successMessage, 'success');
        } catch (error) {
            setMessage(error.message, 'error');
            state.lifecycleBusy = '';
            if (sourceButton) {
                sourceButton.disabled = false;
                sourceButton.textContent = '按反馈继续修改';
            } else {
                renderBody();
            }
        }
    }

    function beginPolling() {
        if (pollTimer) clearTimeout(pollTimer);
        if (!state.run || (state.run.status !== 'queued' && state.run.status !== 'running')) return;
        pollTimer = setTimeout(async function () {
            try {
                var result = await api.getCodeStageRun(state.project.id, state.run.id);
                state.run = result.run;
                await loadRunUsage();
                state.codeRuns = state.codeRuns.map(function (item) { return item.id === state.run.id ? state.run : item; });
                if (state.run.status === 'completed') {
                    resetCodePreview();
                    resetLifecycle();
                    await loadLifecycle();
                    renderBody();
                    await loadRunChanges();
                    return;
                }
                renderBody();
                beginPolling();
            } catch (error) {
                setMessage(error.message, 'error');
            }
        }, 2000);
    }

    async function loadProjectData(projectId, preferredSourceId) {
        state.project = state.projects.find(function (item) { return item.id === projectId; }) || null;
        if (!state.project) return renderBody();
        try {
            var responses = await Promise.all([
                api.listSourceWorkspaces(projectId), api.listArtifacts(projectId, false),
                api.listWorkflowRuns({projectId: projectId}), api.listCodeStageRuns(projectId)
            ]);
            state.sources = responses[0].items || [];
            state.artifacts = responses[1].items || [];
            state.workflowRuns = responses[2].items || [];
            state.codeRuns = responses[3].items || [];
            state.source = state.sources.find(function (item) { return item.id === preferredSourceId; }) || state.sources[0] || null;
            state.run = state.codeRuns.find(function (item) { return state.source && item.sourceWorkspaceId === state.source.id; }) || null;
            await Promise.all([loadRunUsage(), loadLifecycle()]);
            resetCodePreview();
            var sourceSelect = document.getElementById('code-source-select');
            sourceSelect.innerHTML = options(state.sources, state.source && state.source.id, function (item) { return item.id; }, function (item) { return item.name + ' · ' + item.fileCount + ' 个文件'; }, '请上传项目或从空白开始');
            sourceSelect.disabled = !state.sources.length;
            renderBody();
            if (state.run && state.run.status === 'completed') await loadRunChanges();
            beginPolling();
        } catch (error) {
            setMessage(error.message, 'error');
            renderBody();
        }
    }

    async function mount() {
        root = document.getElementById('code-workspace-page');
        if (!root || !window.OxyGentApp || !window.OxyGentApp.api) return;
        api = window.OxyGentApp.api;
        root.innerHTML = shell();
        document.getElementById('upload-project-folder').addEventListener('click', openFolderPicker);
        document.getElementById('create-empty-source').addEventListener('click', createBlankSource);
        document.getElementById('project-folder-input').addEventListener('change', importFolder);
        document.getElementById('code-project-select').addEventListener('change', function (event) { loadProjectData(event.target.value); });
        document.getElementById('code-source-select').addEventListener('change', async function (event) {
            state.source = state.sources.find(function (item) { return item.id === event.target.value; }) || null;
            await selectRun(state.codeRuns.find(function (item) { return state.source && item.sourceWorkspaceId === state.source.id; }) || null);
        });
        try {
            var result = await api.listProjects();
            state.projects = result.items || [];
            var requested = new URLSearchParams(window.location.search).get('project');
            var selected = state.projects.some(function (item) { return item.id === requested; }) ? requested : (state.projects[0] && state.projects[0].id || '');
            document.getElementById('code-project-select').innerHTML = options(state.projects, selected, function (item) { return item.id; }, function (item) { return item.name; }, '请先创建项目');
            document.getElementById('code-project-select').disabled = !state.projects.length;
            await loadProjectData(selected);
        } catch (error) {
            setMessage(error.message, 'error');
            renderNoProject();
        }
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
})();
