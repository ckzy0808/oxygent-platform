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
        selectMode('general');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
    } else {
        mount();
    }
})();
