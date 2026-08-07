/**
 * Render a UML-style sequence diagram.
 *
 * Participants are columns; messages are ordered via DFS tree-walk so that
 * return arrows appear after all child calls complete.
 */
function renderSwimlane(nodesDatas, containerId) {
    var container = document.getElementById(containerId);
    container.innerHTML = '';

    var COL_WIDTH = 150;
    var ROW_HEIGHT = 45;
    var BODY_PAD = 10;

    if (!nodesDatas || nodesDatas.length === 0) {
        container.innerHTML = '<p style="text-align:center;color:#999;padding:20px;">暂无可显示的节点</p>';
        return;
    }

    // ── Build node map ──
    var nodeMap = {};
    nodesDatas.forEach(function (n) { if (n.node_id) nodeMap[n.node_id] = n; });

    // ── Find root node ──
    var rootNode = nodesDatas.find(function (n) { return !n.father_node_id; });
    if (!rootNode) {
        container.innerHTML = '<p style="text-align:center;color:#999;padding:20px;">未找到根节点</p>';
        return;
    }

    // ── Build message list via DFS (skips root → no "user" participant) ──
    var messages = [];

    function sortByTime(ids) {
        return (ids || [])
            .map(function (id) { return nodeMap[id]; })
            .filter(function (n) { return n; })
            .sort(function (a, b) {
                if (!a.create_time) return 1;
                if (!b.create_time) return -1;
                return a.create_time < b.create_time ? -1 : a.create_time > b.create_time ? 1 : 0;
            });
    }

    function walkNode(node) {
        // Forward arrow: caller → callee
        messages.push({
            from: node.caller,
            to: node.callee,
            label: _seqMsgLabel(node),
            dashed: false,
            nodeId: node.node_id
        });

        // Recurse into children (sorted by create_time)
        sortByTime(node.child_node_ids).forEach(function (child) {
            walkNode(child);
        });

        // Return arrow: callee → caller (after all children)
        if (node.output) {
            var outText = typeof node.output === 'string' ? node.output : JSON.stringify(node.output);
            if (outText.length > 50) outText = outText.substring(0, 47) + '...';
            messages.push({
                from: node.callee,
                to: node.caller,
                label: outText,
                dashed: true,
                nodeId: node.node_id,
                isReturn: true
            });
        }
    }

    // Walk children of root (root itself is skipped)
    sortByTime(rootNode.child_node_ids).forEach(function (child) {
        walkNode(child);
    });

    if (messages.length === 0) {
        container.innerHTML = '<p style="text-align:center;color:#999;padding:20px;">暂无可显示的消息</p>';
        return;
    }

    // ── Collect participants from generated messages (order by first appearance) ──
    var participants = [];
    var pSet = {};
    messages.forEach(function (msg) {
        [msg.from, msg.to].forEach(function (name) {
            if (name && !pSet[name]) { pSet[name] = true; participants.push(name); }
        });
    });

    // Map participant → node_type
    var pType = {};
    nodesDatas.forEach(function (n) {
        if (n.callee && n.node_type) pType[n.callee] = n.node_type;
    });
    participants.forEach(function (p) {
        if (!pType[p]) pType[p] = 'agent';
    });

    // ── Layout ──
    var totalWidth = participants.length * COL_WIDTH;
    var bodyHeight = messages.length * ROW_HEIGHT + BODY_PAD * 2;

    var diagram = document.createElement('div');
    diagram.className = 'seq-diagram';
    diagram.style.minWidth = totalWidth + 'px';

    // Header
    diagram.appendChild(_seqParticipantRow(participants, pType, COL_WIDTH));

    // Body
    var body = document.createElement('div');
    body.className = 'seq-body';
    body.style.height = bodyHeight + 'px';
    body.style.minWidth = totalWidth + 'px';

    // Lifelines
    participants.forEach(function (_, i) {
        var ll = document.createElement('div');
        ll.className = 'seq-lifeline';
        ll.style.left = (i * COL_WIDTH + COL_WIDTH / 2) + 'px';
        body.appendChild(ll);
    });

    // Messages
    messages.forEach(function (msg, i) {
        var fromIdx = participants.indexOf(msg.from);
        var toIdx = participants.indexOf(msg.to);
        if (fromIdx === -1 || toIdx === -1 || fromIdx === toIdx) return;

        var y = BODY_PAD + i * ROW_HEIGHT;
        var fromX = fromIdx * COL_WIDTH + COL_WIDTH / 2;
        var toX = toIdx * COL_WIDTH + COL_WIDTH / 2;
        var leftX = Math.min(fromX, toX);
        var width = Math.abs(toX - fromX);
        var isRight = toIdx > fromIdx;

        var msgEl = document.createElement('div');
        msgEl.className = 'seq-msg';
        if (msg.nodeId) {
            msgEl.setAttribute('data-node-id', msg.nodeId);
            msgEl.classList.add('seq-msg-clickable');
            if (!msg.isReturn) {
                msgEl.id = msg.nodeId;
            }
        }
        msgEl.style.top = y + 'px';

        // Label
        var label = document.createElement('div');
        label.className = 'seq-msg-label';
        label.style.left = leftX + 'px';
        label.style.width = width + 'px';
        label.textContent = msg.label;
        label.title = msg.label;
        msgEl.appendChild(label);

        // Arrow line
        var line = document.createElement('div');
        line.className = 'seq-msg-line';
        if (msg.dashed) line.classList.add('seq-msg-dashed');
        line.classList.add(isRight ? 'seq-arrow-right' : 'seq-arrow-left');
        line.style.left = leftX + 'px';
        line.style.width = width + 'px';
        msgEl.appendChild(line);

        body.appendChild(msgEl);
    });

    diagram.appendChild(body);

    // Footer
    diagram.appendChild(_seqParticipantRow(participants, pType, COL_WIDTH));

    container.appendChild(diagram);
}

/* ── Helpers ── */

/** Build a row of participant boxes. */
function _seqParticipantRow(participants, pType, colWidth) {
    var row = document.createElement('div');
    row.className = 'seq-header';
    participants.forEach(function (name) {
        var col = document.createElement('div');
        col.className = 'seq-participant';
        col.style.width = colWidth + 'px';

        var box = document.createElement('div');
        box.className = 'seq-participant-box';

        var img = document.createElement('img');
        img.alt = '';
        var type = pType[name] || 'agent';
        if ((type === 'agent' || type === 'flow') && agent_id_dict && agent_id_dict[name] !== undefined) {
            var idx = agent_id_dict[name] % 16;
            var cur = agentImgMap[idx];
            img.src = cur.imgUrl;
            img.style.backgroundColor = cur.bgColor;
        } else {
            img.src = typeMap[type] || typeMap.tool || './image/org_tool.svg';
            img.style.backgroundColor = '#E1EFFD';
        }
        box.appendChild(img);

        var span = document.createElement('span');
        span.className = 'seq-participant-name';
        span.textContent = name.length > 12 ? name.substring(0, 10) + '..' : name;
        span.title = name;
        box.appendChild(span);

        col.appendChild(box);
        row.appendChild(col);
    });
    return row;
}

/** Derive a short label for the forward (call) arrow. */
function _seqMsgLabel(node) {
    if (node.input && node.input.arguments) {
        var q = node.input.arguments.query;
        if (q && typeof q === 'string' && q.length > 0) {
            return q.length > 50 ? q.substring(0, 47) + '...' : q;
        }
    }
    return node.callee;
}
