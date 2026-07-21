(function () {
    'use strict';

    window.OxyGentApp = window.OxyGentApp || {};

    async function request(path, options) {
        var response = await fetch('../api/v1/platform' + path, Object.assign({
            headers: {'Content-Type': 'application/json'}
        }, options || {}));
        var payload;
        try {
            payload = await response.json();
        } catch (_error) {
            payload = {};
        }
        if (!response.ok) {
            var message = payload.detail || payload.message || 'Platform request failed';
            throw new Error(message);
        }
        return payload.data || {};
    }

    window.OxyGentApp.api = {
        capabilities: function () { return request('/capabilities'); },
        listProjects: function () { return request('/projects'); },
        createProject: function (project) {
            return request('/projects', {method: 'POST', body: JSON.stringify(project)});
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
        listUsage: function () { return request('/usage'); },
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
        }
    };
})();
