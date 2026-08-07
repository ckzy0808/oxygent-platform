// View 4 — Timeline Chain
// Renders all llm/tool nodes for the current trace_id, sorted by create_time
// ascending, connected as a horizontal chain. Reuses globals defined in
// node.html: `typeMap` for icon paths.
//
// Exposed as both `renderTraceChain` and (legacy alias) `renderTimeline`.

function renderTraceChain(nodesDatas, containerId) {
    var container = (typeof containerId === 'string')
        ? document.getElementById(containerId) : containerId;
    if (!container) return;
    container.innerHTML = '';

    // Fallback icon map when `typeMap` is not globally available (e.g. index.html).
    var _typeMap = (typeof typeMap !== 'undefined') ? typeMap : {
        llm: './image/org_model.svg',
        tool: './image/org_tool.svg',
        model: './image/org_model.svg',
    };

    if (!nodesDatas || !nodesDatas.length) {
        container.innerHTML = '<div class="view4-empty">此追踪中暂无节点。</div>';
        return;
    }

    // Filter to llm/tool only.
    var filtered = nodesDatas.filter(function (n) {
        return n && (n.node_type === 'llm' || n.node_type === 'tool');
    });

    if (!filtered.length) {
        container.innerHTML = '<div class="view4-empty">此追踪中暂无 LLM 或工具调用。</div>';
        return;
    }

    // Sort by create_time ascending. ISO-ish strings sort correctly
    // lexicographically; fall back to Date.parse for safety.
    filtered.sort(function (a, b) {
        var at = a.create_time || '';
        var bt = b.create_time || '';
        if (at === bt) return 0;
        if (at < bt) return -1;
        return 1;
    });

    var chain = document.createElement('div');
    chain.className = 'view4-chain';

    // ── Start sentinel node ──
    chain.appendChild(makeSentinel('start'));
    chain.appendChild(makeArrow());

    filtered.forEach(function (n, i) {
        var nodeEl = document.createElement('div');
        nodeEl.className = 'view4-node';
        nodeEl.id = n.node_id;
        nodeEl.setAttribute('data-node-id', n.node_id);
        if (n.frameIndex != null) {
            nodeEl.setAttribute('data-frame-index', n.frameIndex);
        }

        var iconSrc = (_typeMap && _typeMap[n.node_type]) || '';
        var iconHtml = iconSrc
            ? '<img class="view4-icon" alt="" src="' + iconSrc + '" />'
            : '';

        var caller = n.caller || '';

        nodeEl.innerHTML =
            (caller
                ? '<div class="view4-caller-row">' +
                  '<span class="view4-caller" title="' + escapeAttr(caller) + '">' +
                  escapeHtml(caller) +
                  '</span>' +
                  '</div>'
                : '<div class="view4-caller-row"></div>') +
            '<div class="view4-main-row">' +
            iconHtml +
            '<span class="view4-callee" title="' + escapeAttr(n.callee || '') + '">' +
            escapeHtml(n.callee || '') +
            '</span>' +
            '</div>';

        chain.appendChild(nodeEl);

        if (i < filtered.length - 1) {
            chain.appendChild(makeArrow());
        }
    });

    // ── End sentinel node ──
    chain.appendChild(makeArrow());
    chain.appendChild(makeSentinel('end'));

    container.appendChild(chain);

    function makeArrow() {
        var arrow = document.createElement('div');
        arrow.className = 'view4-arrow';
        arrow.innerHTML = '<svg width="24" height="14" viewBox="0 0 28 14"><path d="M2 7h20m0 0l-5-4m5 4l-5 4" fill="none" stroke="#B0B5C2" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        return arrow;
    }

    // Build a Start or End sentinel node, styled to match the same Start/End
    // boxes used by the Trace Graph in index.html.
    function makeSentinel(kind) {
        var el = document.createElement('div');
        if (kind === 'start') {
            el.className = 'view4-node view4-node-start';
            el.title = '开始';
            el.innerHTML =
                '<div class="view4-main-row">' +
                '<svg class="view4-icon-svg" width="20" height="20" viewBox="0 0 18 18">' +
                '<circle cx="9" cy="9" r="7" fill="#22c55e" stroke="#16a34a" stroke-width="1.5"/>' +
                '<polygon points="7.5,5.5 13,9 7.5,12.5" fill="#fff"/>' +
                '</svg>' +
                '<span class="view4-callee">开始</span>' +
                '</div>';
        } else {
            el.className = 'view4-node view4-node-end';
            el.title = '结束';
            el.innerHTML =
                '<div class="view4-main-row">' +
                '<svg class="view4-icon-svg" width="20" height="20" viewBox="0 0 18 18">' +
                '<circle cx="9" cy="9" r="7" fill="#ef4444" stroke="#dc2626" stroke-width="1.5"/>' +
                '<rect x="6.5" y="6.5" width="5" height="5" rx="0.5" fill="#fff"/>' +
                '</svg>' +
                '<span class="view4-callee">结束</span>' +
                '</div>';
        }
        return el;
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }
    function escapeAttr(s) {
        return escapeHtml(s).replace(/"/g, '&quot;');
    }
}
