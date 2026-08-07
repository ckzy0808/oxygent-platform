function formatTimeWithMilliseconds(timeString) {
    // 拆分日期和时间部分
    const [datePart, timePart] = timeString.split(' ');

    // 拆分时间部分
    const [hms, fractions] = timePart.split('.');

    // 提取前3位毫秒（如果不足3位则补0）
    const milliseconds = fractions ? fractions.substring(0, 3).padEnd(3, '0') : '000';

    // 组合成新格式
    return `${datePart} ${hms}.${milliseconds}`;
}

function unionName(data) {
    return [...new Set(data)]
}


function generateGrant(taskList) {
    let ganttCode = `gantt \n`;
    ganttCode += `dateFormat  YYYY-MM-DD HH:mm:ss.SSS \n`;
    // 添加节(Section)
    // ganttCode += `    section 项目阶段\n`;
    // 添加任务
    taskList.forEach(task => {
        if (task.sectionName) {
            ganttCode += `section ${task.sectionName}\n`;
        }
        if (task.data) {
            task.data.forEach(_task => {
                ganttCode += `    ${_task.name} :${_task.id}, ${_task.start}, ${_task.end}\n`;
                ganttCode += `    click ${_task.id} call renderGanttClick(${_task.id})\n`;
                ganttCode += '\n';
            })
        }

    });
    return ganttCode;
}


function bumpMillisecond(timeStr) {
    // Add 1ms to a formatted time string "YYYY-MM-DD HH:mm:ss.SSS"
    var parts = timeStr.split('.');
    var ms = parseInt(parts[1], 10) + 1;
    return parts[0] + '.' + String(ms).padStart(3, '0');
}


function renderGantt(nodesDatas, containerId) {

    const callerArray = nodesDatas.filter(_data => _data.create_time).map(({caller}) => caller);
    const unionNamecallerArray = unionName(callerArray);

    const transformData = nodesDatas
        .filter(_data => _data.create_time)
        .map(_data => {
            const endTime = _data.update_time || _data.create_time;
            const startFormatted = formatTimeWithMilliseconds(_data.create_time);
            let endFormatted = formatTimeWithMilliseconds(endTime);
            if (endFormatted === startFormatted) {
                endFormatted = bumpMillisecond(endFormatted);
            }
            return {
                ..._data,
                id: _data.node_id,
                next: _data.child_node_ids.filter(i => i),
                name: _data.callee,
                start: startFormatted,
                end: endFormatted,
            }
        })

    const transformDataSection = unionNamecallerArray.map((_caller) => {
        return {
            sectionName: _caller,
            data: transformData.filter(i => i.caller === _caller),
        }
    })

    const code = generateGrant(transformDataSection);
    $('#flowchart-container-gantt').html('').removeAttr('data-processed');
    const container = document.getElementById(containerId);
    if (transformData.length === 0) {
        container.innerHTML = '<p style="text-align:center;color:#999;padding:20px;">暂无已完成节点</p>';
        return;
    }
    container.innerHTML = code;
    mermaid.run({
        querySelector: `#${containerId}`
    }).then(function () {
        truncateSectionTitles(containerId);
    });
}


/**
 * After mermaid renders the gantt SVG, section-title <text> elements on the
 * left can be arbitrarily wide and spill over the bars.  This helper
 * measures each title's rendered width against the available space (the x
 * coordinate of the first grid column) and truncates with "..." when needed.
 */
function truncateSectionTitles(containerId) {
    var container = document.getElementById(containerId);
    if (!container) return;
    var svg = container.querySelector('svg');
    if (!svg) return;

    // The bars live inside a <g> whose first <rect.task> tells us the left
    // edge of the chart area.  Fall back to a generous 160px if nothing found.
    var firstTask = svg.querySelector('rect.task');
    var maxRight = firstTask ? firstTask.getBBox().x - 8 : 160;

    var titles = svg.querySelectorAll('text[class*="sectionTitle"]');
    titles.forEach(function (txt) {
        var original = txt.getAttribute('data-original-text') || txt.textContent;
        txt.setAttribute('data-original-text', original);

        // Always (re)attach a <title> child so the full caller name is shown
        // as a native tooltip on hover, even when truncated.
        var existingTitle = txt.querySelector('title');
        if (existingTitle) existingTitle.remove();
        var titleEl = document.createElementNS('http://www.w3.org/2000/svg', 'title');
        titleEl.textContent = original;

        // Reset to full text so we can measure from scratch
        txt.textContent = original;
        var bbox = txt.getBBox();
        var textLeft = bbox.x;
        if (textLeft + bbox.width <= maxRight) {
            txt.appendChild(titleEl);
            return;  // fits — nothing to truncate
        }

        // Trim characters until the text + "..." fits.
        var s = original;
        while (s.length > 1) {
            s = s.slice(0, -1);
            txt.textContent = s + '...';
            if (txt.getBBox().width + textLeft <= maxRight) {
                txt.appendChild(titleEl);
                return;
            }
        }
        txt.textContent = '...';
        txt.appendChild(titleEl);
    });
}







