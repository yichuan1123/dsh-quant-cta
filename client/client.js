// dsh-quant-cta — client half（浏览器端）
//
// 宿主端已经提供了工具与 /dsh-quant-cta/* 路由；这一半只做一件事：
// 把「量化 CTA 工作台」挂进右侧栏的「开始」列表，点开就是一个 tab。
//
// tab 内容直接用 iframe 指向 /dsh-quant-cta/panel —— 面板本身是宿主路由
// 直接吐的静态页（同源），这样不用把整套 UI 重写成 React，也不会出现
// 「两份实现各自漂移」的问题。
//
// 这个文件是经典脚本（不是 ESM）：dsh 的 client-modules 用 CJS factory 执行它。

window.__ModuleLoader__.load({
  id: 'dsh-quant-cta',
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;

    var React = require('react');
    var h = React.createElement;

    var PLUGIN_VERSION = '0.11.0';
    var TAB_ID = 'dsh-quant-cta';
    var TAB_KIND = 'quantcta';
    var PANEL_URL = '/dsh-quant-cta/panel';

    /** tab 内容：同源 iframe 套面板页。 */
    function QuantCtaPanel() {
      return h('iframe', {
        src: PANEL_URL,
        title: '量化 CTA 工作台',
        style: {
          width: '100%',
          height: '100%',
          minHeight: '520px',
          border: '0',
          display: 'block',
          background: '#0f1115',
        },
        // 刻意不加 sandbox：
        // ① 面板是同源、自己写的静态页，没有隔离需求；
        // ② sandbox 缺 allow-modals 会把面板里「清除凭据」的 confirm() 静默拦掉，
        //    表现为按钮点了没反应——这类坑比它挡住的威胁更实际。
      });
    }

    /** tab 定义。guide 数组就是「开始」列表里的那一项。 */
    function tabDefinition() {
      return {
        id: TAB_ID,
        kind: TAB_KIND,
        priority: 'extension',
        title: () => '📈 量化 CTA',
        guide: [
          {
            order: 95,
            title: () => '量化 CTA 工作台',
            description: () => '期货回测、模拟盘与数据源账号管理',
          },
        ],
      };
    }

    /** 用到的宿主端服务（client ctx 注入声明）。 */
    var inject = ['slots', 'sidebarRightTabs', 'sidebarRight'];

    function apply(ctx) {
      try {
        ctx.effect(
          () => ctx.sidebarRightTabs.register(tabDefinition()),
          'dsh-quant-cta: 右侧栏 tab 类型',
        );
      } catch (error) {
        console.error('[dsh-quant-cta] tab 类型注册失败', error);
      }
      try {
        ctx.effect(
          () =>
            ctx.slots.inject('sidebar.right.pane.tab', () =>
              ctx.slots.register({ name: 'sidebar.right.pane.tab', key: TAB_ID }, QuantCtaPanel),
            ),
          'dsh-quant-cta: 右侧栏 tab 内容',
        );
      } catch (error) {
        console.error('[dsh-quant-cta] tab 内容注册失败', error);
      }
      if (typeof window !== 'undefined') {
        window.__dshQuantCta = {
          version: PLUGIN_VERSION,
          kind: TAB_KIND,
          panel: PANEL_URL,
          open: () => {
            try {
              ctx.sidebarRight.openTab(TAB_KIND);
            } catch (error) {
              console.warn('[dsh-quant-cta] 打开 tab 失败，可手动点「开始」里的入口', error);
            }
          },
        };
      }
    }

    module.exports = {
      apply: apply,
      inject: inject,
      PLUGIN_VERSION: PLUGIN_VERSION,
      __internals: { TAB_ID: TAB_ID, TAB_KIND: TAB_KIND, tabDefinition: tabDefinition, QuantCtaPanel: QuantCtaPanel, PANEL_URL: PANEL_URL },
    };
    return module.exports;
  },
});
