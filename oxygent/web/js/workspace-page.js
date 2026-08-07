(function () {
    'use strict';

    var pages = {
        projects: {
            eyebrow: '项目工作区',
            title: '项目',
            description: '在不改变现有对话流程的前提下，组织创意、需求、架构、任务、产物和智能体团队。',
            tabs: ['概览', '创意', '需求', '架构', '任务', '产物', '团队', '活动', '设置'],
            next: ['项目列表和详情 API', '产物来源和修订记录', '将对话转换为项目任务']
        },
        files: {
            eyebrow: '共享上下文',
            title: '文件',
            description: '用于管理对话附件和项目文件引用。源代码仓库始终隔离在代码工作区中。',
            tabs: ['最近使用', '附件', '项目文件', '产物'],
            next: ['附件引用', '项目隔离', '安全预览和下载']
        },
        agents: {
            eyebrow: '智能体注册表',
            title: '智能体',
            description: '查看角色分配、模型策略、能力、工具策略、运行状态、Token 用量和成功率。',
            tabs: ['团队', '角色', '智能体配置', '工具策略'],
            next: ['角色与模型映射', '运行状态', '用量和成功率指标']
        },
        models: {
            eyebrow: '模型控制面',
            title: '模型',
            description: '管理服务商、模型配置、路由策略、健康状态和用量，同时始终隐藏凭证。',
            tabs: ['服务商', '模型', '路由策略', '用量'],
            next: ['服务商健康检查', '主模型和备用模型链', '仅保存凭证引用']
        },
        workflows: {
            eyebrow: '结构化协作',
            title: '工作流',
            description: '以工程阶段和产物追踪角色驱动的工作，不将执行过程表现为普通群聊。',
            tabs: ['定义', '运行记录', '时间线', '产物'],
            next: ['版本化工作流事件', '阶段状态投影', '高级执行详情抽屉']
        },
        insights: {
            eyebrow: '运营与用量',
            title: '洞察',
            description: '按项目和角色了解真实 Token、计量方式、延迟、成功率、故障切换和任务结果。',
            tabs: ['概览', '用量', '可靠性'],
            next: ['项目级聚合', '角色和模型明细', '预算预警状态']
        },
        settings: {
            eyebrow: '平台配置',
            title: '设置',
            description: '配置平台默认值、安全边界、工作区根目录和功能开关，同时避免泄露任何密钥。',
            tabs: ['常规', '安全', '工作区', '功能'],
            next: ['能力检测', '仓库允许列表', '本地和生产环境保护措施']
        },
        code: {
            eyebrow: '工程工作区',
            title: '代码',
            description: '在这里集中查看仓库上下文、工程阶段、代码变更和验证结果。',
            tabs: ['代码仓库', '代码任务', '变更', '审查', '验证'],
            next: ['隔离的 Git 工作树', '系统强制执行的变更契约', '固定参数的验证命令'],
            code: true
        }
    };

    function escapeHtml(value) {
        return String(value).replace(/[&<>'"]/g, function (character) {
            return ({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                "'": '&#39;',
                '"': '&quot;'
            })[character];
        });
    }

    function codeFoundation() {
        var columns = [
            ['仓库上下文', 6],
            ['任务时间线', 7],
            ['变更与验证', 5]
        ];
        return '<section class="og-foundation-card og-code-foundation">' +
            columns.map(function (column, index) {
                var placeholders = '';
                for (var i = 0; i < column[1]; i += 1) {
                    placeholders += '<div class="og-code-placeholder' +
                        (i === index + 1 ? ' emphasis' : '') + '"></div>';
                }
                return '<div class="og-code-column"><p class="og-code-column-label">' +
                    escapeHtml(column[0]) + '</p>' + placeholders + '</div>';
            }).join('') +
        '</section>';
    }

    function mount() {
        var root = document.getElementById('workspace-page');
        if (!root) return;
        var pageId = document.body.getAttribute('data-oxygent-page');
        var page = pages[pageId];
        if (!page) return;
        document.title = page.title + ' - OxyGent';
        root.innerHTML =
            '<header class="og-workspace-header">' +
                '<div><p class="og-workspace-eyebrow">' + escapeHtml(page.eyebrow) + '</p>' +
                '<h1 class="og-workspace-title">' + escapeHtml(page.title) + '</h1></div>' +
                '<span class="og-preview-badge">基础功能</span>' +
            '</header>' +
            '<div class="og-workspace-content">' +
                '<nav class="og-section-tabs" aria-label="' + escapeHtml(page.title) + '栏目">' +
                    page.tabs.map(function (tab, index) {
                        return '<span class="og-section-tab' + (index === 0 ? ' active' : '') + '">' +
                            escapeHtml(tab) + '</span>';
                    }).join('') +
                '</nav>' +
                '<div class="og-foundation-grid">' +
                    '<section class="og-foundation-card"><h2>工作区基础功能已就绪</h2>' +
                    '<p>' + escapeHtml(page.description) + '</p>' +
                    '<div class="og-empty-visual" aria-hidden="true"><div class="og-empty-block"></div>' +
                    '<div class="og-empty-block"></div><div class="og-empty-block"></div></div></section>' +
                    '<aside class="og-foundation-card"><h2>后续建设内容</h2><ul class="og-foundation-list">' +
                    page.next.map(function (item) { return '<li>' + escapeHtml(item) + '</li>'; }).join('') +
                    '</ul></aside>' +
                    (page.code ? codeFoundation() : '') +
                '</div>' +
            '</div>';
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
    } else {
        mount();
    }
})();
