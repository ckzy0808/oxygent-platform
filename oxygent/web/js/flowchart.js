
function renderFlowchart(agentNodes, containerId) {
    function getNodeDisplayInfo(node) {
        let displayName, nodeType;

        if (node.node_type === 'agent') {
            displayName = node.callee || node.node_id;
            nodeType = 'agent';
        } else if (node.node_type === 'llm') {
            displayName = node.callee || 'llm';
            nodeType = 'llm';
        } else if (node.node_type === 'tool') {
            displayName = node.callee || 'tool';
            nodeType = 'tool';
        } else if (node.node_type === 'bank') {
            displayName = node.callee || 'bank';
            nodeType = 'bank';
        } else {
            displayName = node.node_id;
            nodeType = 'agent';
        }

        // 缩短过长的显示名称
        displayName = displayName.length > 15 ?
            `${displayName.substring(0, 12)}...` : displayName;

        return { displayName, nodeType, callee: node.callee };
    }

    function createNodeElement(node) {
        const { displayName, nodeType, callee } = getNodeDisplayInfo(node);

        let preImg;
        if (nodeType === 'tool') {
            preImg = `<img style="padding: 2px;" src="${typeMap.tool}" alt="">`;
        } else if (nodeType === 'llm') {
            preImg = `<img style="padding: 2px;" src="${typeMap.llm}" alt="">`;
        } else if (nodeType === 'bank') {
            preImg = `<img style="padding: 2px;" src="${typeMap.bank}" alt="">`;
        } else {
            const rawIdx = agent_id_dict ? agent_id_dict[callee] : undefined;
            const idx = (rawIdx != null ? rawIdx : 0) % 16;
            const cur = agentImgMap[idx];

            preImg = `<img style="background-color: ${cur?.bgColor}; border-radius: 4px;" src="${cur?.imgUrl}" alt="">`;
        }

        if (node.child_node_ids && node.child_node_ids.length > 0) {
            const container = document.createElement('div');
            container.className = 'view1-container';
            container.id = node.node_id;

            const label = document.createElement('div');
            label.className = 'view1-label';
            label.innerHTML = `
                ${preImg}
                <span>${displayName}</span>
            `;
            label.onclick = e => {
                e.stopPropagation();
                container.classList.toggle('view1-collapsed');
            };
            container.appendChild(label);

            const content = document.createElement('div');
            content.className = 'view1-content';
            const childFlow = buildFlow(node.child_node_ids, node.node_id);
            childFlow.forEach(el => content.appendChild(el));
            container.appendChild(content);
            return container;
        } else {
            const element = document.createElement('div');
            element.className = 'view1-node';
            element.innerHTML = `
                ${preImg}
                <span class="hhh">${displayName}</span>
            `;
            element.id = node.node_id;
            return element;
        }
    }

    // Straight arrow for 1-to-1 connections.
    function createArrow() {
        const arrow = document.createElement('div');
        arrow.className = 'arrow';
        arrow.innerHTML = '<svg width="24" height="14" viewBox="0 0 28 14"><path d="M2 7h20m0 0l-5-4m5 4l-5 4" fill="none" stroke="#286DFF" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        return arrow;
    }

    // Horizontal line from previous node to the fork vertical spine.
    function createForkLine() {
        const el = document.createElement('span');
        el.className = 'view1-fork-line';
        return el;
    }

    // Horizontal arrow from the join vertical spine to the next node.
    function createJoinArrow() {
        const el = document.createElement('span');
        el.className = 'view1-join-arrow';
        el.innerHTML = '<svg width="24" height="14" viewBox="0 0 24 14"><path d="M2 7h16m-4 -3l4 3-4 3" fill="none" stroke="#286DFF" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        return el;
    }

    function createSentinel(kind) {
        const el = document.createElement('div');
        if (kind === 'start') {
            el.className = 'view1-node view1-node-start';
            el.title = '开始';
            el.innerHTML =
                '<svg class="view1-icon-svg" width="14" height="14" viewBox="0 0 18 18">' +
                '<circle cx="9" cy="9" r="7" fill="#22c55e" stroke="#16a34a" stroke-width="1.5"/>' +
                '<polygon points="7.5,5.5 13,9 7.5,12.5" fill="#fff"/>' +
                '</svg>' +
                '<span>开始</span>';
        } else {
            el.className = 'view1-node view1-node-end';
            el.title = '结束';
            el.innerHTML =
                '<svg class="view1-icon-svg" width="14" height="14" viewBox="0 0 18 18">' +
                '<circle cx="9" cy="9" r="7" fill="#ef4444" stroke="#dc2626" stroke-width="1.5"/>' +
                '<rect x="6.5" y="6.5" width="5" height="5" rx="0.5" fill="#fff"/>' +
                '</svg>' +
                '<span>结束</span>';
        }
        return el;
    }

    function buildFlow(nodeIds, parentId) {
        const elements = [];
        const processed = new Set();
        let currentIds = nodeIds.filter(id => {
            const node = nodesMap.get(id);
            return !node.pre_node_ids || node.pre_node_ids.length === 0 || node.pre_node_ids.every(preId => !nodeIds.includes(preId))
        });

        // Start sentinel
        elements.push(createSentinel('start'));
        let prevIsParallel = false;

        while (currentIds.length > 0) {
            const nextIds = new Set();

            // Group by parallel_id
            const parallelGroups = new Map();
            const singleNodes = [];

            currentIds.forEach(id => {
                const node = nodesMap.get(id);
                if (node.parallel_id) {
                    if (!parallelGroups.has(node.parallel_id)) {
                        parallelGroups.set(node.parallel_id, []);
                    }
                    parallelGroups.get(node.parallel_id).push(id);
                } else {
                    singleNodes.push(id);
                }
            });

            // Collect all node ids in this wave
            const allIds = [];
            parallelGroups.forEach(ids => ids.forEach(id => allIds.push(id)));
            singleNodes.forEach(id => allIds.push(id));

            const isParallel = allIds.length > 1;

            // ── Insert connector between previous wave and this one ──
            if (prevIsParallel && isParallel) {
                // parallel → parallel: join from previous, then fork into current
                elements.push(createJoinArrow());
                elements.push(createForkLine());
            } else if (prevIsParallel) {
                // parallel → single: join arrow
                elements.push(createJoinArrow());
            } else if (isParallel) {
                // single → parallel: fork line
                elements.push(createForkLine());
            } else {
                // single → single: straight arrow
                elements.push(createArrow());
            }

            // ── Build elements for this wave ──
            if (isParallel) {
                const parallelContainer = document.createElement('div');
                parallelContainer.className = 'view1-parallel-container view1-forked';

                allIds.forEach(id => {
                    const node = nodesMap.get(id);
                    const branch = document.createElement('div');
                    branch.className = 'view1-parallel-branch';

                    const nodeElement = createNodeElement(node);
                    if (nodeElement.classList.contains('view1-container')) {
                        branch.appendChild(nodeElement);
                    } else {
                        const simpleFlow = document.createElement('div');
                        simpleFlow.className = 'view1-flowchart';
                        simpleFlow.appendChild(nodeElement);
                        branch.appendChild(simpleFlow);
                    }

                    // Join hook for right-side connector
                    const joinHook = document.createElement('span');
                    joinHook.className = 'view1-join-hook';
                    branch.appendChild(joinHook);

                    parallelContainer.appendChild(branch);
                    processed.add(id);
                    if (node.post_node_ids) {
                        node.post_node_ids.forEach(nid => nextIds.add(nid));
                    }
                });

                elements.push(parallelContainer);
            } else {
                const id = allIds[0];
                const node = nodesMap.get(id);
                elements.push(createNodeElement(node));
                processed.add(id);
                if (node.post_node_ids) {
                    node.post_node_ids.forEach(nid => nextIds.add(nid));
                }
            }

            prevIsParallel = isParallel;
            currentIds = Array.from(nextIds).filter(id => !processed.has(id));
        }

        // End sentinel
        if (prevIsParallel) {
            elements.push(createJoinArrow());
        } else {
            elements.push(createArrow());
        }
        elements.push(createSentinel('end'));

        return elements;
    }

    const showMaster = localStorage.getItem('flowchart_show_master') !== 'false';
    const rootNode = agentNodes.find(node => !node.father_node_id);
    const nodesMap = new Map(agentNodes.map(node => [node.node_id, node]));

    if (rootNode) {
        const flowchartElements = showMaster
            ? buildFlow([rootNode.node_id], null)
            : buildFlow(rootNode.child_node_ids, rootNode.node_id);
        const container = document.getElementById(containerId);
        $('#flowchart-container').html('').removeAttr('data-processed');
        flowchartElements.forEach(el => container.appendChild(el));
    }
}
