"""Native GPW editor; every physical write starts from a user action."""
import copy
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, colorchooser, filedialog, messagebox
from tkinter.font import Font

from config.settings import ROOT
from providers.gpw_settings import (GpwSettings, MODEL, EFFECTS, BUTTONS, SPECIALS,
                                    MOUSE, MEDIA, key_binding, encode_macro)
from ui.s99_settings import KEYS
from ui.widgets import (BG, SURFACE, TEXT, MUTED, RED, PillButton, SegmentedNotebook,
                        style_controls, device_image)

TARGETS = {name: key_binding('mouse', value) for name, value in MOUSE.items()}
TARGETS.update({name: key_binding('special', value) for value, name in SPECIALS.items()})
TARGETS.update({name: key_binding('media', value) for name, value in MEDIA.items()})
TARGETS['禁用'] = key_binding('disabled')


def binding_name(raw):
    if raw[0] >> 4 in (0, 1):
        return f'板载宏 · 区域 {((raw[0]&15)<<8)|raw[1]}'
    if raw[:2] == b'\x80\x02':
        mod, key = raw[2:]
        names = [name for name, (m, k) in KEYS.items() if k == key and k and not m]
        mods = [name for i, name in enumerate(['Ctrl', 'Shift', 'Alt', 'Win', '右 Ctrl', '右 Shift', '右 Alt', '右 Win']) if mod & (1 << i)]
        return ' + '.join(mods + (names[:1] if names else ([f'键码 {key}'] if key else [])))
    for name, value in TARGETS.items():
        if value == raw or (raw[0] == 0x90 and value[0] == 0x90 and value[1] == raw[1]):
            return name
    return '保留的特殊映射 · '+raw.hex(' ')


class GpwSettingsWindow:
    API_FACTORY = GpwSettings

    def __init__(self, root, on_close=None):
        self.api = self.API_FACTORY()
        self.window = tk.Toplevel(root)
        self.window.title('Logitech GPW 一代 · 设备设置')
        self.window.geometry('840x735')
        self.window.minsize(780, 680)
        self.window.configure(bg=BG)
        self.window.transient(root)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda _: self.close())
        style_controls(self.window)
        self.on_close = on_close
        self.closed = self.busy = False
        self.snapshot = None
        self.original = None
        self.results = queue.Queue()
        self.buttons = []
        self.library = []
        self.local_path = ROOT/'runtime'/'config'/'gpw-macros.json'
        top = tk.Frame(self.window, bg=BG, padx=24, pady=14)
        top.pack(fill='x')
        self.art = device_image(top, 'mouse', 'GPW', compact=True)
        tk.Label(top, image=self.art, bg=BG).pack(side='right')
        tk.Label(top, text='Logitech GPW 一代', bg=BG, fg=TEXT,
                 font=('Microsoft YaHei UI', 20, 'bold')).pack(anchor='w')
        tk.Label(top, text='性能、灯效和按键，保存在鼠标中。', bg=BG, fg=MUTED).pack(anchor='w', pady=(4, 0))
        bar = tk.Frame(self.window, bg=BG, padx=24)
        bar.pack(fill='x', pady=(0, 6))
        self.profile = tk.StringVar(value='1')
        self.profile_box = ttk.Combobox(bar, textvariable=self.profile, values=['1','2','3','4','5'],
                                       width=5, state='readonly')
        self.profile_box.pack(side='left')
        self.profile_box.bind('<<ComboboxSelected>>', lambda _: self.select_profile())
        self.button(bar, '使用此配置', self.activate).pack(side='left', padx=8)
        self.button(bar, '重新读取', self.read_settings).pack(side='right')
        self.overview = tk.Label(self.window, text='正在连接鼠标…', bg=BG, fg=MUTED, anchor='w', padx=24)
        self.overview.pack(fill='x', pady=(0, 12))
        self.tabs = SegmentedNotebook(self.window)
        self.tabs.pack(fill='both', expand=True, padx=24)
        self.status = tk.Label(self.window, text='正在读取板载配置…', bg=BG, fg=MUTED,
                               anchor='w', justify='left', padx=24, pady=12, wraplength=770)
        self.status.pack(side='bottom', fill='x', before=self.tabs.nav)
        self.status.bind('<Configure>', lambda e: self.status.configure(wraplength=max(200,e.width-48)))
        self.performance_page = self.page('性能与灯效')
        self.key_page = self.page('按键映射')
        self.macro_page = self.page('宏')
        self.backup_page = self.page('配置与备份')
        self.build_performance()
        self.build_keys()
        self.build_macros()
        self.build_backups()
        self.timer = self.window.after(80, self.poll)
        try:
            if self.local_path.exists():
                self.library = json.loads(self.local_path.read_text(encoding='utf-8'))
                self.validate_library(self.library)
        except (OSError, ValueError, TypeError, KeyError):
            self.library = []
        self.refresh_macros()
        self.read_settings()

    def page(self, name):
        page = tk.Frame(self.tabs, bg=SURFACE, padx=20, pady=16)
        self.tabs.add(page, text=name)
        return page

    def button(self, parent, text, command):
        font = Font(root=self.window, family='Microsoft YaHei UI', size=10)
        btn = PillButton(parent, text, command, width=font.measure(text)+30,
                         height=max(38, font.metrics('linespace')+16), bg=parent.cget('bg'))
        self.buttons.append(btn)
        return btn

    @staticmethod
    def label(parent, text):
        tk.Label(parent, text=text, bg=SURFACE, fg=MUTED, justify='left', anchor='w',
                 wraplength=720).pack(fill='x', pady=(0, 10))

    def build_performance(self):
        p = self.performance_page
        footer = tk.Frame(p, bg=SURFACE)
        footer.pack(side='bottom', fill='x', pady=(10, 0))
        self.button(footer, '应用性能与灯效', self.apply_settings).pack(side='right')
        tk.Label(footer,text='应用保存到所选配置；立即试用只调整当前 DPI。',bg=SURFACE,fg=MUTED,
                 wraplength=470,justify='left',font=('Microsoft YaHei UI',9)).pack(side='left',fill='x',expand=True)
        form = tk.Frame(p, bg=SURFACE)
        form.pack(fill='x')
        self.name = tk.StringVar()
        tk.Label(form, text='配置名称', bg=SURFACE, fg=TEXT).grid(row=0, column=0, sticky='w', pady=6)
        ttk.Entry(form, textvariable=self.name, width=28).grid(row=0, column=1, sticky='w', padx=12)
        self.rate = tk.StringVar()
        tk.Label(form, text='回报率', bg=SURFACE, fg=TEXT).grid(row=0, column=2, sticky='w')
        self.rate_box = ttk.Combobox(form, textvariable=self.rate, width=12, state='readonly')
        self.rate_box.grid(row=0, column=3, padx=12)
        self.label(p, '五个 DPI 档位；填 0 可禁用。默认与 Shift 档位必须启用。')
        dpis = tk.Frame(p, bg=SURFACE)
        dpis.pack(fill='x')
        self.dpi = []
        for i in range(5):
            tk.Label(dpis, text=f'档位 {i+1}', bg=SURFACE, fg=MUTED).grid(row=0, column=i, sticky='w')
            var = tk.StringVar()
            self.dpi.append(var)
            ttk.Entry(dpis, textvariable=var, width=10).grid(row=1, column=i, padx=(0,14), pady=(4,10))
        stages = tk.Frame(p, bg=SURFACE)
        stages.pack(fill='x', pady=(0,10))
        self.default = tk.StringVar()
        self.shift = tk.StringVar()
        for title, var in [('默认档位',self.default), ('Shift 档位',self.shift)]:
            tk.Label(stages, text=title, bg=SURFACE, fg=TEXT).pack(side='left')
            ttk.Combobox(stages, textvariable=var, values=list(range(1,6)), width=4,
                         state='readonly').pack(side='left', padx=(8,20))
        self.live_dpi = tk.StringVar()
        ttk.Entry(stages, textvariable=self.live_dpi, width=8).pack(side='left')
        self.button(stages, '立即试用 DPI', self.apply_live_dpi).pack(side='left', padx=8)
        self.light_vars = []
        self.light_controls = []
        for z, title in enumerate(['主灯区', 'Logo 灯区']):
            row = tk.LabelFrame(p, text=title, bg=SURFACE, fg=TEXT, padx=12, pady=9,
                                font=('Microsoft YaHei UI',10))
            row.pack(fill='x', pady=6)
            values = {key: tk.StringVar() for key in ('mode','color','period','brightness')}
            self.light_vars.append(values)
            mode = ttk.Combobox(row, textvariable=values['mode'], values=list(EFFECTS.values()),
                                width=10, state='readonly')
            mode.grid(row=0, column=0, padx=(0,8))
            mode.bind('<<ComboboxSelected>>', lambda _, zone=z: self.light_mode_changed(zone))
            color = ttk.Entry(row, textvariable=values['color'], width=10)
            color.grid(row=0, column=1)
            choose = self.button(row, '选色', lambda zone=z: self.pick_color(zone))
            choose.grid(row=0, column=2, padx=8)
            tk.Label(row, text='周期 ms', bg=SURFACE, fg=MUTED).grid(row=0, column=3)
            period = ttk.Entry(row, textvariable=values['period'], width=7)
            period.grid(row=0, column=4, padx=6)
            tk.Label(row, text='亮度 %', bg=SURFACE, fg=MUTED).grid(row=0, column=5)
            bright = ttk.Entry(row, textvariable=values['brightness'], width=5)
            bright.grid(row=0, column=6, padx=(6,0))
            self.light_controls.append((mode,color,choose,period,bright))

    def light_mode_changed(self, zone):
        mode = self.light_vars[zone]['mode'].get()
        _, color, choose, period, bright = self.light_controls[zone]
        color.configure(state='normal' if mode in ('常亮','呼吸') else 'disabled')
        choose.configure(state='normal' if mode in ('常亮','呼吸') and not self.busy else 'disabled')
        period.configure(state='normal' if mode in ('循环变色','呼吸') else 'disabled')
        bright.configure(state='normal' if mode in ('循环变色','呼吸') else 'disabled')

    def pick_color(self, zone):
        color = colorchooser.askcolor(self.light_vars[zone]['color'].get(), parent=self.window)[1]
        if color:
            self.light_vars[zone]['color'].set(color)

    def form(self):
        return {'name': self.name.get(), 'report_ms': 1000//int(self.rate.get().split()[0]),
                'dpi': [int(v.get()) for v in self.dpi], 'default': int(self.default.get())-1,
                'shift': int(self.shift.get())-1,
                'lights': [{'mode': next(k for k,v in EFFECTS.items() if v == vs['mode'].get()),
                            'color': vs['color'].get(), 'period': int(vs['period'].get()),
                            'brightness': int(vs['brightness'].get())} for vs in self.light_vars]}

    def show_profile(self):
        if not self.snapshot:
            return
        n = int(self.profile.get())
        p = self.snapshot['profiles'][n]
        values = p['values']
        self.name.set(values['name'])
        self.rate_box.configure(values=[f'{1000//ms} Hz' for ms in self.snapshot['caps']['rates']])
        self.rate.set(f"{1000//values['report_ms']} Hz")
        for var, v in zip(self.dpi, values['dpi']):
            var.set(str(v))
        self.default.set(str(values['default']+1))
        self.shift.set(str(values['shift']+1))
        for z, vs in enumerate(self.light_vars):
            light = values['lights'][z]
            for key in vs:
                value = EFFECTS.get(light[key], '保留现有效果') if key == 'mode' else light[key]
                if key == 'period' and not 1000 <= value <= 20000:
                    value = 5000  # unused fields must not cause implicit writes
                vs[key].set(str(value))
            supported = self.snapshot['caps']['zones'][z]['effects']
            self.light_controls[z][0].configure(values=[v for k,v in EFFECTS.items() if k in supported])
            self.light_mode_changed(z)
        try:
            self.original = self.form()
        except (ValueError, StopIteration):
            # Unknown light effects stay untouched unless explicitly replaced.
            self.original = self.form_with_unknown()
        self.loaded_profile = n
        self.show_keys()

    def form_with_unknown(self):
        lights = []
        for z, vs in enumerate(self.light_vars):
            mode = next((k for k,v in EFFECTS.items() if v == vs['mode'].get()), -1)
            lights.append({'mode':mode,'color':vs['color'].get(),'period':int(vs['period'].get()),'brightness':int(vs['brightness'].get())})
        return {'name':self.name.get(),'report_ms':1000//int(self.rate.get().split()[0]),
                'dpi':[int(v.get()) for v in self.dpi],'default':int(self.default.get())-1,
                'shift':int(self.shift.get())-1,'lights':lights}

    def select_profile(self):
        if self.busy or not self.snapshot:
            self.profile.set(str(getattr(self,'loaded_profile',1)))
            return
        try:
            dirty = self.original != self.form_with_unknown()
        except ValueError:
            dirty = True
        if dirty and not messagebox.askyesno('切换编辑配置', '放弃尚未应用的修改并查看另一组配置？', parent=self.window):
            self.profile.set(str(self.loaded_profile))
            return
        self.show_profile()
        self.set_status('已切换编辑配置；鼠标仍使用原先的配置。')

    def show_snapshot(self, snap):
        self.snapshot = snap
        self.profile_box.configure(values=[str(i) for i in snap['profiles']])
        if int(self.profile.get()) not in snap['profiles']:
            self.profile.set('1')
        rt = snap['runtime']
        active = next((i for i,p in snap['profiles'].items() if p['sector']==rt['active']), '?')
        self.overview.configure(text=f"{'板载模式' if rt['mode']==1 else '软件控制模式'} · 当前配置 {active} · {rt['dpi']} DPI · {1000//rt['report_ms']} Hz")
        self.live_dpi.set(str(rt['dpi']))
        self.show_profile()
        backup = snap.get('backup')
        self.set_status('已完成并回读校验；原值已自动备份。' if backup else '已读取五组板载配置；点击应用后才会修改鼠标。')

    def read_settings(self):
        if self.has_unsaved() and not messagebox.askyesno('重新读取', '放弃尚未应用的性能与灯效修改？', parent=self.window):
            return
        self.run(self.api.read_settings, self.show_snapshot, '正在读取鼠标的板载配置…')

    def has_unsaved(self):
        if not self.snapshot or self.original is None:
            return False
        try:
            return self.form_with_unknown() != self.original
        except (ValueError, KeyError):
            return True

    def require_saved_form(self):
        if self.has_unsaved():
            self.set_status('性能与灯效有尚未应用的修改；请先应用或重新读取。', True)
            return False
        return True

    def apply_settings(self):
        if not self.ready():
            return
        try:
            values = self.form_with_unknown()
            changes = {k:v for k,v in values.items() if k != 'lights' and v != self.original[k]}
            lights = {z:v for z,v in enumerate(values['lights']) if v != self.original['lights'][z]}
            if lights:
                changes['lights'] = lights
            if not changes:
                self.set_status('没有修改，无需写入。')
                return
        except (ValueError, StopIteration):
            self.set_status('请检查 DPI、档位、周期与亮度的输入。', True)
            return
        n, expected = int(self.profile.get()), self.snapshot
        self.run(lambda:self.api.apply_profile(n,changes,expected), self.show_snapshot, '正在备份并保存板载配置…')

    def activate(self):
        if not self.ready() or not self.require_saved_form():
            return
        n, expected = int(self.profile.get()), self.snapshot
        self.run(lambda:self.api.activate_profile(n,expected), self.show_snapshot, '正在启用并切换板载配置…')

    def apply_live_dpi(self):
        if not self.ready() or not self.require_saved_form():
            return
        try:
            value = int(self.live_dpi.get())
        except ValueError:
            self.set_status('请输入有效 DPI 整数。', True)
            return
        expected = self.snapshot
        self.run(lambda:self.api.set_current_dpi(value,expected), self.show_snapshot, '正在应用当前 DPI…')

    def build_keys(self):
        p = self.key_page
        self.label(p, '选择实体按键后设置功能。普通层始终保留一个鼠标左键；G-Shift 需先绑定一个 G-Shift 按键。')
        bar = tk.Frame(p, bg=SURFACE)
        bar.pack(fill='x')
        self.layer = tk.StringVar(value='普通层')
        ttk.Combobox(bar,textvariable=self.layer,values=['普通层','G-Shift 层'],state='readonly',width=16).pack(side='left')
        self.layer.trace_add('write',lambda *_:self.show_keys())
        footer = tk.Frame(p, bg=SURFACE)
        footer.pack(side='bottom', fill='x', pady=(12,0))
        self.target = tk.StringVar(value='鼠标后退')
        self.target_box = ttk.Combobox(footer, textvariable=self.target,
                                      values=list(TARGETS)+['键盘：'+name for name in KEYS], state='readonly', width=26)
        self.target_box.grid(row=0,column=0,columnspan=3,sticky='w')
        self.modifiers = {}
        for i, name in enumerate(['Ctrl','Shift','Alt','Win']):
            var = tk.BooleanVar()
            self.modifiers[name] = var
            ttk.Checkbutton(footer,text=name,variable=var).grid(row=1,column=i,pady=10,sticky='w')
        self.button(footer,'应用此按键',self.apply_key).grid(row=0,column=4,padx=(15,0))
        self.key_tree = ttk.Treeview(p,columns=('button','action'),show='headings',height=8,selectmode='browse')
        self.key_tree.heading('button',text='鼠标按键')
        self.key_tree.heading('action',text='当前映射')
        self.key_tree.column('button',width=200)
        self.key_tree.column('action',width=420)
        self.key_tree.pack(fill='both',expand=True,pady=(12,0))

    def show_keys(self):
        if not hasattr(self,'key_tree'):
            return
        self.key_tree.delete(*self.key_tree.get_children())
        if not self.snapshot:
            return
        key = 'keys' if self.layer.get() == '普通层' else 'gkeys'
        for i, raw in enumerate(self.snapshot['profiles'][int(self.profile.get())]['values'][key]):
            self.key_tree.insert('', 'end', iid=str(i), values=(BUTTONS[i],binding_name(raw)))

    def apply_key(self):
        if not self.ready() or not self.require_saved_form():
            return
        selected = self.key_tree.selection()
        if not selected:
            self.set_status('先选择要修改的鼠标按键。',True)
            return
        target = self.target.get()
        if target.startswith('键盘：'):
            mod,key = KEYS[target[3:]]
            mod |= sum(1<<i for i,v in enumerate(self.modifiers.values()) if v.get())
            binding = key_binding('key',key,mod)
        else:
            binding = TARGETS[target]
        layer = 'keys' if self.layer.get() == '普通层' else 'gkeys'
        n, expected = int(self.profile.get()), self.snapshot
        changes = {layer:{int(selected[0]):binding}}
        self.run(lambda:self.api.apply_profile(n,changes,expected),self.show_snapshot,'正在备份并保存按键映射…')

    def build_macros(self):
        p = self.macro_page
        self.label(p, '创建执行一次的键盘宏，保存到鼠标的空闲存储区并绑定按键；支持组合键与毫秒延迟。')
        bar = tk.Frame(p,bg=SURFACE)
        bar.pack(fill='x')
        self.macro_name = tk.StringVar()
        self.macro_combo = ttk.Combobox(bar,textvariable=self.macro_name,width=23)
        self.macro_combo.pack(side='left')
        self.macro_combo.bind('<<ComboboxSelected>>',lambda _:self.show_macro())
        self.button(bar,'新建',self.new_macro).pack(side='left',padx=8)
        self.button(bar,'删除',self.delete_macro).pack(side='left')
        self.button(bar,'保存到本机',self.save_macros).pack(side='right')
        footer = tk.Frame(p,bg=SURFACE)
        footer.pack(side='bottom',fill='x',pady=(12,0))
        row = tk.Frame(footer,bg=SURFACE)
        row.pack(fill='x')
        self.event_action = tk.StringVar(value='按下')
        self.event_key = tk.StringVar(value='A')
        self.event_delay = tk.StringVar(value='50')
        ttk.Combobox(row,textvariable=self.event_action,values=['按下','释放'],state='readonly',width=6).pack(side='left')
        ttk.Combobox(row,textvariable=self.event_key,values=list(KEYS),state='readonly',width=15).pack(side='left',padx=6)
        ttk.Spinbox(row,textvariable=self.event_delay,from_=0,to=65535,width=7).pack(side='left')
        self.button(row,'添加动作',self.add_event).pack(side='left',padx=6)
        self.button(row,'删除动作',self.delete_event).pack(side='left')
        target = tk.Frame(footer,bg=SURFACE)
        target.pack(fill='x',pady=(12,0))
        self.macro_button = tk.StringVar(value=BUTTONS[3])
        self.macro_layer = tk.StringVar(value='普通层')
        ttk.Combobox(target,textvariable=self.macro_button,values=BUTTONS,state='readonly',width=15).pack(side='left')
        ttk.Combobox(target,textvariable=self.macro_layer,values=['普通层','G-Shift 层'],state='readonly',width=12).pack(side='left',padx=8)
        self.button(target,'写入并绑定宏',self.install_macro).pack(side='right')
        self.label(footer,'延迟发生在该动作之后。保留现有宏；空间不足时停止写入。板载回读不代表已验证实际触发。')
        self.event_tree = ttk.Treeview(p,columns=('action','key','delay'),show='headings',height=6,selectmode='browse')
        for col,title in [('action','动作'),('key','按键'),('delay','延迟 ms')]:
            self.event_tree.heading(col,text=title)
            self.event_tree.column(col,width=200)
        scrollbar = ttk.Scrollbar(p,orient='vertical',command=self.event_tree.yview)
        self.event_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y',pady=(10,0))
        self.event_tree.pack(fill='both',expand=True,pady=(10,0))
        self.active_macro = None

    @staticmethod
    def validate_library(library):
        if not isinstance(library,list) or len(library)>32:
            raise ValueError('宏库格式错误或数量超过 32')
        names = set()
        for m in library:
            if not isinstance(m,dict) or set(m) != {'name','events'} or not isinstance(m['name'],str) or not 1<=len(m['name'])<=40 or m['name'] in names:
                raise ValueError('宏名称无效或重复')
            names.add(m['name'])
            encode_macro(m['events'],253)

    def refresh_macros(self):
        self.macro_combo.configure(values=[m['name'] for m in self.library])
        self.macro_name.set(self.library[0]['name'] if self.library else '')
        self.show_macro()

    def show_macro(self):
        self.active_macro = next((m for m in self.library if m['name']==self.macro_name.get()),None)
        self.event_tree.delete(*self.event_tree.get_children())
        if self.active_macro:
            for i,e in enumerate(self.active_macro['events']):
                label = binding_name(bytes((0x80,2,e['modifiers'],e['key'])))
                self.event_tree.insert('','end',iid=str(i),values=('按下' if e['action']=='down' else '释放',label,e['delay']))

    def new_macro(self):
        if len(self.library)>=32:
            self.set_status('本地最多保存 32 个宏。',True)
            return
        name = self.macro_name.get().strip()
        names = {m['name'] for m in self.library}
        i = 1
        if not name or name in names:
            while f'新宏 {i}' in names:
                i += 1
            name = f'新宏 {i}'
        if len(name)>40:
            self.set_status('宏名称最长 40 个字符。',True)
            return
        self.library.append({'name':name,'events':[]})
        self.macro_combo.configure(values=[m['name'] for m in self.library])
        self.macro_name.set(name)
        self.show_macro()

    def delete_macro(self):
        if self.active_macro:
            self.library.remove(self.active_macro)
        self.refresh_macros()

    def add_event(self):
        if not self.active_macro:
            self.set_status('先新建或选择一个宏。',True)
            return
        try:
            delay = int(self.event_delay.get())
            mod,key = KEYS[self.event_key.get()]
            if not 0<=delay<=65535:
                raise ValueError()
        except (ValueError,KeyError):
            self.set_status('请选择按键，延迟范围为 0–65535 毫秒。',True)
            return
        self.active_macro['events'].append({'key':key,'modifiers':mod,
                                            'action':'down' if self.event_action.get()=='按下' else 'up','delay':delay})
        self.show_macro()

    def delete_event(self):
        selected = self.event_tree.selection()
        if self.active_macro and selected:
            del self.active_macro['events'][int(selected[0])]
            self.show_macro()

    def save_macros(self):
        try:
            self.validate_library(self.library)
            self.local_path.parent.mkdir(parents=True,exist_ok=True)
            temporary = self.local_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.library,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.local_path)
            self.set_status('宏已保存到本机。')
        except (ValueError,OSError) as exc:
            self.set_status(str(exc),True)

    def install_macro(self):
        if not self.ready() or not self.active_macro:
            self.set_status('先读取设备并选择一个宏。',True)
            return
        if not self.require_saved_form():
            return
        events = copy.deepcopy(self.active_macro['events'])
        n, expected = int(self.profile.get()), self.snapshot
        button = BUTTONS.index(self.macro_button.get())
        layer = 'keys' if self.macro_layer.get() == '普通层' else 'gkeys'
        self.run(lambda:self.api.install_macro(n,button,layer,events,expected),self.show_snapshot,'正在备份、分配空闲存储区并绑定宏…')

    def build_backups(self):
        p = self.backup_page
        self.label(p,'每次写入先保存原始板载数据，完成后逐字节回读。恢复前也会备份当前值。')
        self.button(p,'打开自动备份文件夹',self.open_backups).pack(anchor='w',pady=6)
        self.button(p,'从备份恢复',self.restore).pack(anchor='w',pady=6)
        self.label(p,'导出与导入包含当前编辑的性能、灯效和本地宏。导入只填入编辑器；按键映射保留在原始备份中。')
        self.button(p,'导出本地配置',self.export_profile).pack(anchor='w',pady=6)
        self.button(p,'导入到编辑器',self.import_profile).pack(anchor='w',pady=6)
        self.label(p,'日常使用可通过板载模式运行，无需 G HUB 常驻。游戏自动切换、Lua 脚本、屏幕/音乐联动和固件更新仍需原软件。')
        self.label(p,'当前适配 GPW 一代 LIGHTSPEED 接收器；不将有线连接或其他罗技型号视为已支持。')

    def open_backups(self):
        folder = ROOT/'runtime'/'backups'/'gpw'
        folder.mkdir(parents=True,exist_ok=True)
        os.startfile(folder)

    def restore(self):
        if self.busy:
            self.set_status('请等待当前操作完成。')
            return
        path = filedialog.askopenfilename(parent=self.window,initialdir=str(ROOT/'runtime'/'backups'/'gpw'),filetypes=[('GPW 备份','*.json')])
        if path and messagebox.askyesno('恢复 GPW 配置','恢复此备份记录的板载数据和运行状态？当前值会先备份。',parent=self.window):
            expected = self.snapshot
            self.run(lambda:self.api.restore_backup(path,expected),self.show_snapshot,'正在备份当前数据并恢复…')

    def export_profile(self):
        if not self.ready():
            return
        try:
            values = self.form_with_unknown()
            self.validate_library(self.library)
            path = filedialog.asksaveasfilename(parent=self.window,defaultextension='.json',filetypes=[('GPW 配置','*.json')])
            if path:
                Path(path).write_text(json.dumps({'schema':1,'model':MODEL,'values':values,'macros':self.library},ensure_ascii=False,indent=2),encoding='utf-8')
                self.set_status('已导出本地配置。')
        except (ValueError,OSError) as exc:
            self.set_status(str(exc),True)

    def import_profile(self):
        if not self.ready():
            return
        path = filedialog.askopenfilename(parent=self.window,filetypes=[('GPW 配置','*.json')])
        if not path:
            return
        try:
            if Path(path).stat().st_size > 131072:
                raise ValueError('配置文件过大')
            obj = json.loads(Path(path).read_text(encoding='utf-8'))
            if obj.get('schema') != 1 or obj.get('model') != MODEL:
                raise ValueError('配置文件型号不匹配')
            values = obj['values']
            self.validate_library(obj['macros'])
            from providers.gpw_settings import patch_profile
            edits = values | {'lights':{z:v for z,v in enumerate(values['lights'])}}
            patch_profile(self.snapshot['profiles'][int(self.profile.get())]['raw'],edits,self.snapshot['caps'])
            self.name.set(values['name'])
            self.rate.set(f"{1000//values['report_ms']} Hz")
            for var,v in zip(self.dpi,values['dpi']):
                var.set(str(v))
            self.default.set(str(values['default']+1))
            self.shift.set(str(values['shift']+1))
            for z,vs in enumerate(self.light_vars):
                for k in vs:
                    vs[k].set(EFFECTS[values['lights'][z][k]] if k=='mode' else str(values['lights'][z][k]))
                self.light_mode_changed(z)
            self.library = obj['macros']
            self.refresh_macros()
            self.set_status('已导入编辑器；点击应用后才会修改鼠标。')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self.set_status('导入失败：'+str(exc),True)

    def ready(self):
        if self.busy:
            self.set_status('请等待当前操作完成。')
            return False
        if not self.snapshot:
            self.set_status('请先重新读取鼠标配置。',True)
            return False
        return True

    def set_status(self, text, error=False):
        self.status.configure(text=text,fg=RED if error else MUTED)

    def run(self, operation, done, text):
        if self.busy or self.closed:
            return
        self.busy = True
        self.profile_box.configure(state='disabled')
        for button in self.buttons:
            button.configure(state='disabled')
        self.set_status(text)
        def worker():
            try:
                self.results.put((done,operation(),None))
            except Exception as exc:
                self.results.put((done,None,str(exc)))
        threading.Thread(target=worker,daemon=True,name='GPW-settings').start()

    def poll(self):
        if self.closed:
            return
        try:
            done,result,error = self.results.get_nowait()
            self.busy = False
            self.profile_box.configure(state='readonly')
            for button in self.buttons:
                button.configure(state='normal')
            if error:
                self.snapshot = self.original = None
                self.overview.configure(text='读取或应用未完成 · 请重新读取')
                self.set_status(error,True)
            else:
                try:
                    done(result)
                except Exception as exc:
                    self.snapshot = self.original = None
                    self.set_status('无法显示设备配置，请重新读取：'+str(exc),True)
            for z in range(2):
                self.light_mode_changed(z)
        except queue.Empty:
            pass
        self.timer = self.window.after(80,self.poll)

    def close(self):
        if self.closed:
            return True
        if self.busy:
            self.set_status('正在与鼠标通信，完成后即可关闭。')
            return False
        self.closed = True
        self.window.after_cancel(self.timer)
        self.window.destroy()
        if self.on_close:
            self.on_close()
        return True
