(function () {
    'use strict';

    window.OxyGentApp = window.OxyGentApp || {};

    function localizeMessage(message) {
        var text = String(message || '');
        var exact = {
            'Platform request failed': '平台请求失败',
            'Code Workspace is not configured': '代码工作区尚未配置',
            'Provider mutations are disabled': '服务商修改已被禁用'
        };
        if (exact[text]) return exact[text];
        return text
            .replace(/not found/gi, '未找到')
            .replace(/is unavailable/gi, '不可用')
            .replace(/is disabled/gi, '已禁用')
            .replace(/not configured/gi, '尚未配置');
    }

    var sourceImportLimits = {maxFiles: 3000, maxTotalBytes: 80 * 1024 * 1024, maxFileBytes: 8 * 1024 * 1024};
    var ignoredSourceParts = new Set([
        '.conda-env', '.coverage', '.git', '.idea', '.mypy_cache', '.pytest_cache', '.ruff_cache',
        '.tox', '.venv', '.vscode', '__pycache__', 'build', 'coverage', 'dist',
        'node_modules', 'target', 'venv'
    ]);
    var ignoredSourceNames = new Set(['.ds_store', 'desktop.ini', 'thumbs.db']);

    function prepareSourceFiles(files) {
        var accepted = [];
        var paths = [];
        var skipped = 0;
        var totalBytes = 0;
        Array.prototype.forEach.call(files || [], function (file) {
            var path = file.webkitRelativePath || file.name || '';
            var parts = path.split('/').filter(Boolean).map(function (part) { return part.toLowerCase(); });
            var name = parts[parts.length - 1] || '';
            var ignored = parts.some(function (part) {
                return ignoredSourceParts.has(part) || part.indexOf('.aider') === 0;
            }) || ignoredSourceNames.has(name) || name === '.env' || name.indexOf('.env.') === 0 || /\.(key|pem|p12|pfx)$/i.test(name);
            if (ignored) {
                skipped += 1;
                return;
            }
            if (file.size > sourceImportLimits.maxFileBytes) {
                throw new Error('文件过大，无法导入：' + path + '（单个文件上限 8 MB）');
            }
            if (accepted.length >= sourceImportLimits.maxFiles) {
                throw new Error('项目文件过多。过滤依赖和构建目录后仍超过 3000 个文件。');
            }
            if (totalBytes + file.size > sourceImportLimits.maxTotalBytes) {
                throw new Error('项目源码过大。过滤依赖和构建目录后仍超过 80 MB。');
            }
            accepted.push(file);
            paths.push(path);
            totalBytes += file.size;
        });
        if (!accepted.length) {
            throw new Error('所选文件夹没有可导入的源码文件。依赖、构建产物、虚拟环境和凭证文件会被自动跳过。');
        }
        return {files: accepted, paths: paths, skipped: skipped, totalBytes: totalBytes};
    }

    async function request(path, options) {
        options = options || {};
        var defaults = {};
        if (!(options.body instanceof FormData)) defaults.headers = {'Content-Type': 'application/json'};
        var response;
        try {
            response = await fetch('../api/v1/platform' + path, Object.assign(defaults, options));
        } catch (error) {
            if (error && error.name === 'AbortError') throw new Error('请求超时，请缩小项目后重试。');
            throw new Error('无法连接本地 OxyGent 服务。请确认服务仍在运行；如果正在上传项目，请移除大型依赖或构建目录后重试。');
        }
        var payload;
        try {
            payload = await response.json();
        } catch (_error) {
            payload = {};
        }
        if (!response.ok) {
            var message = payload.detail || payload.message || 'Platform request failed';
            throw new Error(localizeMessage(message));
        }
        return payload.data || {};
    }

    function insightQuery(filters) {
        var params = new URLSearchParams();
        Object.keys(filters || {}).forEach(function (key) {
            var value = filters[key];
            if (value != null && value !== '') params.set(key, value);
        });
        return params.toString() ? '?' + params.toString() : '';
    }

    window.OxyGentApp.api = {
        capabilities: function () { return request('/capabilities'); },
        listProjects: function () { return request('/projects'); },
        createProject: function (project) {
            return request('/projects', {method: 'POST', body: JSON.stringify(project)});
        },
        updateProject: function (projectId, project) {
            return request('/projects/' + encodeURIComponent(projectId), {method: 'PATCH', body: JSON.stringify(project)});
        },
        deleteProject: function (projectId) {
            return request('/projects/' + encodeURIComponent(projectId), {method: 'DELETE'});
        },
        getProject: function (projectId) { return request('/projects/' + encodeURIComponent(projectId)); },
        listTasks: function (projectId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/tasks');
        },
        createTaskFromChat: function (projectId, task) {
            return request('/projects/' + encodeURIComponent(projectId) + '/tasks/from-chat', {
                method: 'POST',
                body: JSON.stringify(task)
            });
        },
        listArtifacts: function (projectId, latestOnly) {
            var suffix = latestOnly ? '?latestOnly=true' : '';
            return request('/projects/' + encodeURIComponent(projectId) + '/artifacts' + suffix);
        },
        listActivity: function (projectId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/activity');
        },
        listRoles: function () { return request('/roles'); },
        listAgents: function () { return request('/agents'); },
        listToolPolicies: function () { return request('/tool-policies'); },
        listProviders: function () { return request('/providers'); },
        createProvider: function (provider) {
            return request('/providers', {method: 'POST', body: JSON.stringify(provider)});
        },
        updateProvider: function (providerId, provider) {
            return request('/providers/' + encodeURIComponent(providerId), {
                method: 'PATCH', body: JSON.stringify(provider)
            });
        },
        testProvider: function (providerId, modelId) {
            return request('/providers/' + encodeURIComponent(providerId) + '/test-connection', {
                method: 'POST', body: JSON.stringify({modelId: modelId || null})
            });
        },
        listModels: function () { return request('/models'); },
        listRoutingPolicies: function () { return request('/routing-policies'); },
        listUsage: function (filters) { return request('/usage' + insightQuery(filters || {})); },
        getInsightsSummary: function (filters) {
            return request('/insights/summary' + insightQuery(filters));
        },
        getInsightsBreakdown: function (dimension, filters) {
            return request('/insights/breakdown' + insightQuery(Object.assign({dimension: dimension}, filters || {})));
        },
        listInsightRuns: function (filters) {
            return request('/insights/runs' + insightQuery(filters));
        },
        listWorkflowRuns: function (filters) {
            var params = new URLSearchParams();
            filters = filters || {};
            if (filters.projectId) params.set('projectId', filters.projectId);
            if (filters.taskId) params.set('taskId', filters.taskId);
            return request('/workflows/runs' + (params.toString() ? '?' + params.toString() : ''));
        },
        getWorkflowRun: function (runId) {
            return request('/workflows/runs/' + encodeURIComponent(runId));
        },
        listWorkflowEvents: function (runId) {
            return request('/workflows/runs/' + encodeURIComponent(runId) + '/events');
        },
        startProjectWorkflow: function (projectId, workflow) {
            return request('/projects/' + encodeURIComponent(projectId) + '/workflows/runs', {
                method: 'POST', body: JSON.stringify(workflow)
            });
        },
        workflowEventStreamUrl: function (runId) {
            return '../api/v1/platform/workflows/runs/' + encodeURIComponent(runId) + '/stream';
        },
        listSourceWorkspaces: function (projectId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/source-workspaces');
        },
        listSourceAnalyses: function (projectId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/source-analyses');
        },
        analyzeSourceWorkspace: function (projectId, sourceWorkspaceId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/source-workspaces/' + encodeURIComponent(sourceWorkspaceId) + '/analyze', {method: 'POST'});
        },
        createBlankSourceWorkspace: function (projectId, name) {
            return request('/projects/' + encodeURIComponent(projectId) + '/source-workspaces/blank', {
                method: 'POST', body: JSON.stringify({name: name || '空白项目'})
            });
        },
        importSourceWorkspace: function (projectId, files, name) {
            var selection = prepareSourceFiles(files);
            var form = new FormData();
            selection.files.forEach(function (file) {
                form.append('files', file, file.name);
            });
            form.append('pathsJson', JSON.stringify(selection.paths));
            form.append('name', name || (selection.paths[0] ? selection.paths[0].split('/')[0] : '上传的项目'));
            return request('/projects/' + encodeURIComponent(projectId) + '/source-workspaces/import', {
                method: 'POST', body: form
            }).then(function (result) {
                var source = result.sourceWorkspace || {};
                result.importStats = {
                    discoveredCount: selection.files.length + selection.skipped,
                    fileCount: Number(source.fileCount || 0),
                    skipped: selection.skipped + Number(source.skippedFileCount || 0),
                    totalBytes: Number(source.totalBytes || selection.totalBytes || 0)
                };
                return result;
            });
        },
        listCodeStageRuns: function (projectId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-stage-runs');
        },
        startCodeStageRun: function (projectId, payload) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-stage-runs', {
                method: 'POST', body: JSON.stringify(payload)
            });
        },
        getCodeStageRun: function (projectId, runId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-stage-runs/' + encodeURIComponent(runId));
        },
        getCodeStageChanges: function (projectId, runId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-stage-runs/' + encodeURIComponent(runId) + '/changes');
        },
        getCodeStageFileChange: function (projectId, runId, path) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-stage-runs/' + encodeURIComponent(runId) + '/changes/' + path.split('/').map(encodeURIComponent).join('/'));
        },
        getCodeStageLifecycle: function (projectId, runId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-stage-runs/' + encodeURIComponent(runId) + '/lifecycle');
        },
        verifyCodeStage: function (projectId, runId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-stage-runs/' + encodeURIComponent(runId) + '/verify', {method: 'POST'});
        },
        reviewCodeStage: function (projectId, runId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-stage-runs/' + encodeURIComponent(runId) + '/review', {method: 'POST'});
        },
        overrideCodeStageReview: function (projectId, runId, payload) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-stage-runs/' + encodeURIComponent(runId) + '/review-override', {method: 'POST', body: JSON.stringify(payload)});
        },
        startCodeStageReviewRevision: function (projectId, runId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-stage-runs/' + encodeURIComponent(runId) + '/review-revision', {method: 'POST'});
        },
        approveCodeStage: function (projectId, runId, payload) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-stage-runs/' + encodeURIComponent(runId) + '/approve', {method: 'POST', body: JSON.stringify(payload)});
        },
        codeStageDownloadUrl: function (projectId, runId) {
            return '../api/v1/platform/projects/' + encodeURIComponent(projectId) + '/code-stage-runs/' + encodeURIComponent(runId) + '/download';
        },
        codeStageFileUrl: function (projectId, runId, path) {
            return '../api/v1/platform/projects/' + encodeURIComponent(projectId) + '/code-stage-runs/' + encodeURIComponent(runId) + '/files/' + path.split('/').map(encodeURIComponent).join('/');
        },
        listRepositorySources: function () { return request('/code/repository-sources'); },
        listRepositories: function (projectId) {
            var suffix = projectId ? '?projectId=' + encodeURIComponent(projectId) : '';
            return request('/code/repositories' + suffix);
        },
        registerRepository: function (projectId, repository) {
            return request('/projects/' + encodeURIComponent(projectId) + '/repositories', {
                method: 'POST', body: JSON.stringify(repository)
            });
        },
        listCodeTasks: function (projectId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks');
        },
        createCodeTask: function (projectId, task) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks', {
                method: 'POST', body: JSON.stringify(task)
            });
        },
        getCodeTask: function (projectId, taskId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId));
        },
        getRepositoryMetadata: function (projectId, taskId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/repository/metadata');
        },
        getRepositoryTree: function (projectId, taskId, path) {
            var suffix = path ? '?path=' + encodeURIComponent(path) : '';
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/repository/tree' + suffix);
        },
        searchRepository: function (projectId, taskId, query, path) {
            var params = new URLSearchParams({query: query});
            if (path) params.set('path', path);
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/repository/search?' + params.toString());
        },
        readRepositoryFile: function (projectId, taskId, path) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/repository/file?path=' + encodeURIComponent(path));
        },
        getCodeTaskDiff: function (projectId, taskId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/diff');
        },
        generateCodePreview: function (projectId, taskId, instructions) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/code-preview', {
                method: 'POST', body: JSON.stringify({instructions: instructions || ''})
            });
        },
        listVerificationProfiles: function (projectId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/verification-profiles');
        },
        createVerificationProfile: function (projectId, profile) {
            return request('/projects/' + encodeURIComponent(projectId) + '/verification-profiles', {
                method: 'POST', body: JSON.stringify(profile)
            });
        },
        listVerificationRuns: function (projectId, taskId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/verification-runs');
        },
        runVerification: function (projectId, taskId, profileId, commandId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/verification-runs', {
                method: 'POST', body: JSON.stringify({profileId: profileId, commandId: commandId})
            });
        },
        getVerificationOutput: function (projectId, taskId, outputId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/verification-outputs/' + encodeURIComponent(outputId));
        },
        listApprovals: function (projectId, taskId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/approvals');
        },
        requestRevision: function (projectId, taskId, payload) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/request-revision', {method: 'POST', body: JSON.stringify(payload)});
        },
        approveChanges: function (projectId, taskId, payload) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/approve', {method: 'POST', body: JSON.stringify(payload)});
        },
        applyChanges: function (projectId, taskId, payload) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/apply', {method: 'POST', body: JSON.stringify(payload)});
        },
        exportPatch: function (projectId, taskId, payload) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/export-patch', {method: 'POST', body: JSON.stringify(payload)});
        },
        discardCodeTask: function (projectId, taskId, payload) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/discard', {method: 'POST', body: JSON.stringify(payload)});
        },
        getRecoveryPatch: function (projectId, taskId, patchId) {
            return request('/projects/' + encodeURIComponent(projectId) + '/code-tasks/' + encodeURIComponent(taskId) + '/recovery-patches/' + encodeURIComponent(patchId));
        }
    };
})();
