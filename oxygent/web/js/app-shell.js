(function () {
    'use strict';

    var icons = {
        chat: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h7A2.5 2.5 0 0 1 16 5.5v4a2.5 2.5 0 0 1-2.5 2.5H9l-3.5 3v-3A2.5 2.5 0 0 1 3 9.5z"/>',
        projects: '<rect x="3" y="5" width="14" height="11" rx="2"/><path d="M7 5V3h6v2M3 9h14"/>',
        code: '<path d="m7 6-4 4 4 4M13 6l4 4-4 4M11.5 3 8.5 17"/>',
        files: '<path d="M5 2.5h6l4 4V17H5z"/><path d="M11 2.5v4h4"/>',
        agents: '<circle cx="10" cy="7" r="3"/><path d="M4.5 17a5.5 5.5 0 0 1 11 0M15 5.5h2M16 4.5v2"/>',
        models: '<rect x="3" y="3" width="14" height="14" rx="3"/><path d="M7 7h6v6H7zM10 1v2M10 17v2M1 10h2M17 10h2"/>',
        workflows: '<circle cx="5" cy="5" r="2"/><circle cx="15" cy="10" r="2"/><circle cx="5" cy="15" r="2"/><path d="M7 5h3a3 3 0 0 1 3 3M7 15h3a3 3 0 0 0 3-3"/>',
        insights: '<path d="M4 16V9M10 16V4M16 16v-6M2 17.5h16"/>',
        settings: '<circle cx="10" cy="10" r="3"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4"/>'
    };

    var primary = [
        ['chat', 'Chat', 'index.html'],
        ['projects', 'Projects', 'projects.html'],
        ['code', 'Code', 'code.html'],
        ['files', 'Files', 'files.html'],
        ['agents', 'Agents', 'agents.html'],
        ['models', 'Models', 'models.html'],
        ['workflows', 'Workflows', 'workflows.html'],
        ['insights', 'Insights', 'insights.html']
    ];

    function item(spec, activePage) {
        var page = spec[0];
        var label = spec[1];
        var href = spec[2];
        var active = page === activePage ? ' active' : '';
        var current = page === activePage ? ' aria-current="page"' : '';
        return '<a class="og-app-nav-item' + active + '" href="' + href +
            '" title="' + label + '"' + current + ' data-nav-page="' + page + '">' +
            '<svg viewBox="0 0 20 20" aria-hidden="true">' + icons[page] + '</svg>' +
            '<span>' + label + '</span></a>';
    }

    function mount() {
        if (document.getElementById('og-app-nav')) return;
        var activePage = document.body.getAttribute('data-oxygent-page') || 'chat';
        var nav = document.createElement('aside');
        nav.id = 'og-app-nav';
        nav.className = 'og-app-nav';
        nav.setAttribute('aria-label', 'Primary navigation');
        nav.innerHTML =
            '<a class="og-app-brand" href="index.html" aria-label="OxyGent Chat">' +
                '<img src="./image/group-favicon.png" alt="OxyGent">' +
            '</a>' +
            '<nav class="og-app-nav-list">' +
                primary.map(function (spec) { return item(spec, activePage); }).join('') +
            '</nav>' +
            '<div class="og-app-nav-footer">' +
                item(['settings', 'Settings', 'settings.html'], activePage) +
            '</div>';
        document.body.insertBefore(nav, document.body.firstChild);
        document.body.classList.add('og-shell-enabled');
    }

    window.OxyGentApp = window.OxyGentApp || {};
    window.OxyGentApp.navigation = primary.slice();

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
    } else {
        mount();
    }
})();
