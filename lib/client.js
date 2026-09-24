/** DSH lazy client bundle. React is supplied by DSH's shared module table. */
window.__ModuleLoader__.load({
  id: 'mimi-desktop-pet',
  factory(require) {
    const React = require('react');
    const { createElement: h, useEffect, useId, useRef, useState, useSyncExternalStore } = React;
    const ID = 'mimi-desktop-pet';
    const KIND = 'mimi-companion';
    const CHANNEL = '/api';

    function createPanelModel(call, { intervalMs = 1000 } = {}) {
      let state = { loading: true, available: false, enabled: true, snapshot: null, busy: false, error: '', actionError: '', feedback: '' };
      let disposed = false;
      let polling = false;
      let sequence = 0;
      let accepted = 0;
      let navigationSeen = 0;
      let timer;
      const listeners = new Set();
      const controllers = new Set();
      const update = patch => {
        if (disposed) return;
        state = { ...state, ...patch };
        for (const listener of listeners) listener();
      };
      const request = async (endpoint, payload) => {
        const controller = new AbortController();
        controllers.add(controller);
        const deadline = setTimeout(() => controller.abort(), 10000);
        try {
          return await call(CHANNEL, `mimi-pet/${endpoint}`, payload, controller.signal);
        } catch {
          return { ok: false, error: { message: '暂时无法连接 DSH，请稍后重试。' } };
        } finally {
          clearTimeout(deadline);
          controllers.delete(controller);
        }
      };
      const refresh = async () => {
        if (disposed || polling || state.busy) return;
        polling = true;
        const seq = ++sequence;
        try {
          const result = await request('state', {});
          if (seq < accepted) return;
          accepted = seq;
          if (result.ok) update({ ...result.value, loading: false, error: '' });
          else update({ available: false, loading: false, error: result.error?.message || '状态同步失败。' });
        } finally { polling = false; }
      };
      const act = async (action, endpoint = 'action') => {
        if (disposed || state.busy) return false;
        const seq = ++sequence;
        // Invalidate any earlier poll as soon as an action begins.
        accepted = seq;
        update({ busy: true, feedback: '', error: '', actionError: '' });
        const result = await request(endpoint, endpoint === 'action' ? { id: crypto.randomUUID(), action } : {});
        if (disposed) return false;
        if (result.ok) {
          update({ ...(result.value.state || (endpoint === 'start' ? result.value : {})),
            busy: false, loading: false, feedback: result.value.message || '', error: '' });
        } else {
          update({ busy: false, actionError: result.error?.message || '操作未完成，请刷新状态后再试。' });
        }
        await refresh();
        return result.ok ? result.value : false;
      };
      return {
        getSnapshot: () => state,
        subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
        refresh,
        act,
        claimNavigation() {
          const nav = state.navigation;
          if (!nav?.revision || nav.revision <= navigationSeen) return null;
          navigationSeen = nav.revision;
          return nav;
        },
        start() {
          void refresh();
          timer = setInterval(() => {
            if (typeof document === 'undefined' || document.visibilityState !== 'hidden') void refresh();
          }, intervalMs);
        },
        dispose() {
          disposed = true;
          clearInterval(timer);
          for (const controller of controllers) controller.abort();
          listeners.clear();
        },
      };
    }

    function localInput(date) {
      const pad = value => String(value).padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }
    function localDate(seconds) {
      return new Date(seconds * 1000).toLocaleString(undefined, {
        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
      });
    }
    function remaining(focus, fallback = 25) {
      const seconds = focus ? Math.max(0, Math.ceil(focus.remaining_s)) : fallback * 60;
      return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
    }

    const CSS = `
.mimi-trigger{display:inline-flex;align-items:center;gap:6px;min-height:28px;padding:3px 7px;border:0;border-radius:6px;background:transparent;color:var(--dsw-alias-label-secondary,#536178);font:inherit;font-size:12px;cursor:pointer}
.mimi-trigger:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(90,130,200,.1))}
.mimi-trigger:focus-visible,.mimi-companion :is(button,input):focus-visible{outline:2px solid var(--dsw-alias-state-business-primary,#5d81df);outline-offset:2px}
.mimi-trigger-count{padding:1px 5px;border-radius:8px;background:var(--dsw-alias-state-business-tertiary,#e7edfe);font-size:11px}
.mimi-companion{box-sizing:border-box;height:100%;min-height:0;overflow:auto;padding:20px 18px 28px;color:var(--dsw-alias-label-primary,#293448);font-family:inherit;font-size:13px;line-height:1.6}
.mimi-companion *{box-sizing:border-box}
.mimi-companion h2{font-size:16px;margin:0 0 4px;font-weight:600}
.mimi-companion h3{font-size:13px;margin:0 0 10px;font-weight:600}
.mimi-companion p{margin:4px 0 10px}
.mimi-companion .mimi-muted{color:var(--dsw-alias-label-tertiary,#788499);font-size:12px}
.mimi-companion .mimi-timer{margin:12px 0 2px;font-size:42px;line-height:1.2;font-variant-numeric:tabular-nums;font-weight:500;letter-spacing:1px;color:var(--dsw-alias-state-business-primary,#597bd1)}
.mimi-companion .mimi-progress{height:3px;border-radius:4px;background:var(--dsw-alias-border-l1,#e7eaf0);overflow:hidden;margin:12px 0 16px}
.mimi-companion .mimi-progress span{display:block;height:100%;background:var(--dsw-alias-state-business-primary,#597bd1);transition:width .2s}
.mimi-companion .mimi-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.mimi-companion button{min-height:32px;padding:5px 10px;border:1px solid var(--dsw-alias-border-l1,#dce2ec);border-radius:7px;background:transparent;color:inherit;font:inherit;font-size:12px;cursor:pointer}
.mimi-companion button:hover:not(:disabled){background:var(--dsw-alias-interactive-bg-hover,rgba(90,130,200,.08))}
.mimi-companion button:disabled{opacity:.45;cursor:default}
.mimi-companion button.mimi-primary{background:var(--dsw-alias-state-business-primary,#597bd1);border-color:transparent;color:white}
.mimi-companion button.mimi-primary:hover:not(:disabled){filter:brightness(1.06);background:var(--dsw-alias-state-business-primary,#597bd1)}
.mimi-companion input:not([type=checkbox]){width:100%;min-width:0;min-height:34px;padding:6px 9px;border:1px solid var(--dsw-alias-border-l1,#dce2ec);border-radius:7px;background:var(--dsw-alias-bg-l1,transparent);color:inherit;font:inherit;font-size:13px}
.mimi-companion .mimi-minutes{display:flex;align-items:center;gap:6px}
.mimi-companion .mimi-minutes input{width:68px}
.mimi-companion .mimi-quiet{display:flex;align-items:flex-start;gap:9px;margin:17px 0 0;cursor:pointer}
.mimi-companion .mimi-quiet input{accent-color:var(--dsw-alias-state-business-primary,#597bd1);margin:5px 0 0;width:15px;height:15px;flex:none}
.mimi-companion .mimi-section{border-top:1px solid var(--dsw-alias-border-l1,#e7eaf0);padding-top:17px;margin-top:18px}
.mimi-companion .mimi-command{display:flex;gap:8px;margin-top:8px}
.mimi-companion .mimi-command button{flex:none}
.mimi-companion .mimi-tabs{display:flex;gap:5px;margin-bottom:14px;flex-wrap:wrap}
.mimi-companion .mimi-tabs button{border-color:transparent;color:var(--dsw-alias-label-tertiary,#788499)}
.mimi-companion .mimi-tabs button[aria-selected=true]{background:var(--dsw-alias-state-business-tertiary,rgba(90,130,200,.1));color:var(--dsw-alias-state-business-primary,#597bd1)}
.mimi-companion form.mimi-reminder-form{display:grid;gap:9px;margin:12px 0 16px}
.mimi-companion form.mimi-reminder-form label{display:grid;gap:4px;color:var(--dsw-alias-label-secondary,#536178);font-size:12px}
.mimi-companion ul{list-style:none;margin:0;padding:0}
.mimi-companion li{padding:12px 0;border-top:1px solid var(--dsw-alias-border-l1,#e7eaf0);overflow-wrap:anywhere}
.mimi-companion .mimi-item-text{font-weight:500;margin:2px 0 8px;white-space:pre-wrap}
.mimi-companion .mimi-item-actions{gap:5px}
.mimi-companion .mimi-item-actions button{padding:3px 8px;min-height:28px}
.mimi-companion .mimi-due{color:var(--dsw-alias-state-warn-label,#ad752f)}
.mimi-companion .mimi-empty{padding:20px 0;text-align:center;color:var(--dsw-alias-label-tertiary,#788499)}
.mimi-companion .mimi-feedback{margin:12px 0 0;padding:8px 10px;border-radius:7px;background:var(--dsw-alias-state-business-tertiary,rgba(90,130,200,.08));overflow-wrap:anywhere}
.mimi-companion .mimi-error{color:var(--dsw-alias-state-warn-label,#ad752f)}
`;

    function apply(ctx) {
      const model = createPanelModel((...args) => ctx.connection.rpc.call(...args));
      ctx.effect(() => {
        const style = document.createElement('style');
        style.dataset.plugin = ID;
        style.dataset.pluginCss = `${ID}/companion`;
        style.textContent = CSS;
        document.head.appendChild(style);
        model.start();
        const visible = () => { if (document.visibilityState === 'visible') void model.refresh(); };
        document.addEventListener('visibilitychange', visible);
        return () => {
          model.dispose();
          document.removeEventListener('visibilitychange', visible);
          style.remove();
        };
      }, 'mimi-pet: companion client state');

      function HeaderAction() {
        const state = useSyncExternalStore(model.subscribe, model.getSnapshot);
        const focus = state.snapshot?.focus;
        const due = state.snapshot?.reminders?.filter(item => item.status === 'due').length || 0;
        useEffect(() => {
          if (document.visibilityState === 'hidden') return;
          const navigation = model.claimNavigation();
          if (navigation) ctx.sidebarRight.openTab(KIND, { params: { section: navigation.section } });
        }, [state.navigation?.revision]);
        if (!state.enabled) return null;
        return h('button', { type: 'button', className: 'mimi-trigger', 'aria-label': 'Mimi 专注与提醒',
          title: '在 DSH 中管理专注与提醒', onClick: () => ctx.sidebarRight.openTab(KIND) },
        h('span', { 'aria-hidden': true }, '◷'), 'Mimi',
        state.available && focus?.status === 'running' ? h('span', null, remaining(focus)) : null,
        due ? h('span', { className: 'mimi-trigger-count', 'aria-label': `${due} 条提醒到期` }, due) : null);
      }

      function CompanionBody(props) {
        const state = useSyncExternalStore(model.subscribe, model.getSnapshot);
        const info = props.useTabInfo();
        const [minutes, setMinutes] = useState(null);
        const [section, setSection] = useState('reminders');
        const [command, setCommand] = useState('');
        const [editor, setEditor] = useState(null);
        const [text, setText] = useState('');
        const [dueAt, setDueAt] = useState(() => localInput(new Date(Date.now() + 20 * 60000)));
        const textRef = useRef(null);
        const sectionId = useId();
        const snapshot = state.snapshot;
        const focus = snapshot?.focus;
        const running = focus?.status === 'running';
        const paused = focus?.status === 'paused';
        const active = running || paused;
        const disabled = state.busy || !state.available || !state.enabled;
        const valueMinutes = minutes === null ? String(snapshot?.default_minutes || state.default_minutes || 25) : minutes;
        const reminders = snapshot?.reminders || [];
        const notices = snapshot?.notices || [];

        useEffect(() => {
          const target = info.tab.navigation.params?.section;
          if (target === 'reminders' || target === 'notices') setSection(target);
        }, [info.tab.navigation.revision]);

        const run = async action => {
          const result = await model.act(action);
          if (result?.section) setSection(result.section);
          return result;
        };
        const resetEditor = () => { setEditor(null); setText(''); };
        const edit = item => {
          setEditor(item.id); setText(item.text); setDueAt(localInput(new Date(item.due_at * 1000)));
          // Focus moves only in response to the user's Edit click.
          requestAnimationFrame(() => textRef.current?.focus());
        };
        const saveReminder = async event => {
          event.preventDefault();
          const result = await run({ op: editor ? 'reminder.update' : 'reminder.add',
            ...(editor ? { id: editor } : {}), text, due_at: new Date(dueAt).getTime() / 1000 });
          if (result) resetEditor();
        };
        const button = (label, action, unavailable = false, primary = false, extra = {}) => h('button', {
          type: 'button', disabled: disabled || unavailable, className: primary ? 'mimi-primary' : '',
          onClick: () => { void run(action); }, ...extra,
        }, label);

        return h('div', { className: 'mimi-companion', 'aria-label': 'Mimi 专注与提醒面板' },
          h('h2', null, '专注陪伴'),
          h('p', { className: 'mimi-muted' }, '陪你把眼前的一件事做好。'),
          state.loading ? h('p', { role: 'status' }, '正在连接 Mimi…') : null,
          !state.enabled ? h('p', { className: 'mimi-muted' }, '请在 DSH 的 mimi-pet 设置中启用专注与提醒，再重启插件。') : null,
          !state.available && !state.loading ? h('div', null,
            h('p', { className: 'mimi-muted', role: 'status' }, state.message || 'Mimi 暂未就绪。'),
            state.pet_enabled && ['stopped', 'error'].includes(state.phase)
              ? h('button', { type: 'button', disabled: state.busy, onClick: () => { void model.act(null, 'start'); } }, '启动 Mimi') : null,
          ) : null,
          h('div', { className: 'mimi-timer', 'aria-label': '剩余时间' }, remaining(focus?.status === 'cancelled' ? null : focus, Number(valueMinutes) || 25)),
          h('p', { className: 'mimi-muted' }, snapshot?.status_text || '准备好就开始吧。'),
          h('div', { className: 'mimi-progress', role: 'progressbar', 'aria-label': '专注进度',
            'aria-valuemin': 0, 'aria-valuemax': 100,
            'aria-valuenow': focus ? Math.round((1 - focus.remaining_s / focus.total_s) * 100) : 0 },
          h('span', { style: { width: `${focus ? Math.max(0, 100 * (1 - focus.remaining_s / focus.total_s)) : 0}%` } })),
          h('div', { className: 'mimi-row' },
            !active ? h('label', { className: 'mimi-minutes' },
              h('input', { type: 'number', min: 1, max: 240, step: 1, value: valueMinutes, disabled,
                'aria-label': '专注时长（分钟）', onChange: event => setMinutes(event.target.value) }), '分钟') : null,
            !active ? button('开始专注', { op: 'focus.start', minutes: Number(valueMinutes) }, snapshot?.system_blocked, true) : null,
            running ? button('暂停', { op: 'focus.pause' }, false, true) : null,
            paused ? button('继续', { op: 'focus.resume' }, snapshot?.system_blocked, true) : null,
            active ? button('取消计时', { op: 'focus.cancel' }) : null,
            !active ? button('休息 5 分钟', { op: 'focus.break', minutes: 5 }, snapshot?.system_blocked) : null,
            focus?.status === 'finished' ? button('结束本次计时', { op: 'focus.dismiss' }) : null,
          ),
          paused ? h('p', { className: 'mimi-muted' }, focus.pause_reason) : null,
          h('label', { className: 'mimi-quiet' },
            h('input', { type: 'checkbox', checked: snapshot?.quiet || false, disabled,
              onChange: event => { void run({ op: 'quiet.set', enabled: event.target.checked }); } }),
            h('span', null, '勿扰', h('div', { className: 'mimi-muted' }, '减少走动和工具提示；提醒保留在列表。'))),
          h('p', { className: 'mimi-muted' }, '锁屏或休眠会暂停计时；回来后手动继续。'),
          state.feedback ? h('p', { className: 'mimi-feedback', role: 'status' }, state.feedback) : null,
          state.error ? h('p', { className: 'mimi-feedback mimi-error', role: 'alert' }, state.error) : null,
          state.actionError ? h('p', { className: 'mimi-feedback mimi-error', role: 'alert' }, state.actionError) : null,
          snapshot?.storage_error ? h('p', { className: 'mimi-error', role: 'alert' }, snapshot.storage_error) : null,
          h('section', { className: 'mimi-section' },
            h('h3', null, '一句话安排'),
            h('form', { className: 'mimi-command', onSubmit: async event => {
              event.preventDefault();
              if (await run({ op: 'command', text: command })) setCommand('');
            } },
            h('input', { type: 'text', value: command, maxLength: 300, required: true, disabled,
              'aria-label': '专注与提醒指令', placeholder: '20 分钟后提醒我喝水',
              onChange: event => setCommand(event.target.value) }),
            h('button', { type: 'submit', disabled: disabled || !command.trim() }, '确定')),
          ),
          h('section', { className: 'mimi-section' },
            h('div', { className: 'mimi-tabs', role: 'tablist', 'aria-label': '提醒与通知' },
              ...[['reminders', `待处理 ${reminders.length}`], ['notices', `最近通知 ${notices.length}`]].map(([key, label]) =>
                h('button', { key, type: 'button', role: 'tab', id: `${sectionId}-${key}-tab`,
                  'aria-selected': section === key, 'aria-controls': `${sectionId}-${key}`,
                  onClick: () => setSection(key) }, label))),
            section === 'reminders' ? h('div', { role: 'tabpanel', id: `${sectionId}-reminders`, 'aria-labelledby': `${sectionId}-reminders-tab` },
              h('form', { className: 'mimi-reminder-form', onSubmit: saveReminder },
                h('label', null, editor ? '修改提醒事项' : '提醒事项',
                  h('input', { ref: textRef, type: 'text', maxLength: 200, required: true, value: text,
                    disabled, placeholder: '例如：喝水、站起来走走', onChange: event => setText(event.target.value) })),
                h('label', null, '提醒时间（本地）',
                  h('input', { type: 'datetime-local', required: true, value: dueAt, disabled,
                    onChange: event => setDueAt(event.target.value) })),
                h('div', { className: 'mimi-row' },
                  h('button', { type: 'submit', className: 'mimi-primary', disabled: disabled || !text.trim() || !dueAt }, editor ? '保存修改' : '添加提醒'),
                  editor ? h('button', { type: 'button', onClick: resetEditor }, '取消修改') : null)),
              reminders.length ? h('ul', { 'aria-label': '待处理提醒' }, ...reminders.map(item => h('li', { key: item.id },
                h('div', { className: item.status === 'due' ? 'mimi-muted mimi-due' : 'mimi-muted' },
                  `${localDate(item.due_at)}${item.status === 'due' ? ' · 已到期' : ''}`),
                h('div', { className: 'mimi-item-text' }, item.text),
                h('div', { className: 'mimi-row mimi-item-actions' },
                  button('完成', { op: 'reminder.update', id: item.id, status: 'done' }, false, false, { 'aria-label': `完成：${item.text}` }),
                  button('稍后 10 分钟', { op: 'reminder.snooze', id: item.id, minutes: 10 }, false, false, { 'aria-label': `稍后提醒：${item.text}` }),
                  h('button', { type: 'button', disabled, onClick: () => edit(item), 'aria-label': `修改：${item.text}` }, '修改'),
                  button('取消', { op: 'reminder.update', id: item.id, status: 'cancelled' }, false, false, { 'aria-label': `取消：${item.text}` }),
                )))) : h('p', { className: 'mimi-empty' }, '还没有提醒，把想记住的事交给 Mimi。'),
            ) : h('div', { role: 'tabpanel', id: `${sectionId}-notices`, 'aria-labelledby': `${sectionId}-notices-tab` },
              notices.length ? h('ul', { 'aria-label': '最近通知' }, ...notices.map(item => h('li', { key: item.id },
                h('div', { className: 'mimi-muted' }, `${localDate(item.created_at)}${item.delivered ? '' : ' · 待提示'}`),
                h('div', { className: 'mimi-item-text' }, item.text)))) : h('p', { className: 'mimi-empty' }, '这里会保留专注和提醒的通知。'),
              notices.length ? button('清空最近通知', { op: 'notices.clear' }) : null,
            ),
          ),
        );
      }

      ctx.effect(() => ctx.sidebarRightTabs.register({ id: ID, kind: KIND,
        title: () => 'Mimi · 专注与提醒',
        guide: [{ id: 'mimi-companion', order: 35, title: () => 'Mimi', description: () => '专注陪伴、勿扰与提醒' }],
      }), 'mimi-pet: companion tab');
      ctx.effect(() => ctx.slots.inject('sidebar.right.pane.tab', () => ctx.slots.register({
        name: 'sidebar.right.pane.tab', key: ID,
      }, CompanionBody)), 'mimi-pet: embedded companion body');
      ctx.effect(() => ctx.slots.inject('conversation.session.header.actions', () => ctx.slots.register({
        name: 'conversation.session.header.actions', id: 'mimi-companion', order: 35,
      }, HeaderAction)), 'mimi-pet: session header entry');
    }

    return { apply, inject: ['slots', 'sidebarRightTabs', 'sidebarRight', 'connection'], createPanelModel, localInput };
  },
});
