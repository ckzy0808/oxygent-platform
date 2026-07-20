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
        }
    };
})();
