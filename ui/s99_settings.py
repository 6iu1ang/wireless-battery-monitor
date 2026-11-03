"""Compact native S99 settings UI. Workers never touch Tk objects."""
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, colorchooser, filedialog, messagebox
from tkinter.font import Font

from config.settings import ROOT
from providers.s99_settings import S99Settings, LIGHT_MODES, patch_settings, encode_macros
from ui.widgets import (BG, SURFACE, TEXT, MUTED, RED, BORDER,
                        style_controls, SegmentedNotebook, device_image, PillButton)

KEYS = {chr(65+i): (0, 4+i) for i in range(26)}
KEYS.update({str((i+1)%10): (0, 30+i) for i in range(10)})
KEYS.update({f'F{i+1}': (0, 58+i) for i in range(12)})
KEYS.update({name: (0, code) for name, code in {
    'Enter':40,'Esc':41,'Backspace':42,'Tab':43,'Space':44,'-':45,'=':46,
    '[':47,']':48,'\\':49,';':51,"'":52,'`':53,',':54,'.':55,'/':56,
    'CapsLock':57,'PrintScreen':70,'ScrollLock':71,'Pause':72,'Insert':73,'Home':74,
    'PageUp':75,'Delete':76,'End':77,'PageDown':78,'Right':79,'Left':80,'Down':81,'Up':82,
    'NumLock':83,'Num /':84,'Num *':85,'Num -':86,'Num +':87,'Num Enter':88,
    'Num 1':89,'Num 2':90,'Num 3':91,'Num 4':92,'Num 5':93,'Num 6':94,
    'Num 7':95,'Num 8':96,'Num 9':97,'Num 0':98,'Num .':99,'Menu':101}.items()})
KEYS.update({name: (1<<i, 0) for i, name in enumerate([
    'Left Ctrl','Left Shift','Left Alt','Left Win','Right Ctrl','Right Shift','Right Alt','Right Win'])})

def key_name(triple):
    if tuple(triple) == (0, 0, 0): return '未设置 / 继承'
    if triple[0] == 32:
        for name, value in KEYS.items():
            if tuple(triple[1:]) == value: return name
    if triple[0] in (112, 113): return f'宏 {triple[1]+1}'
    return '特殊键 ' + ' '.join(f'{v:02X}' for v in triple)

class S99SettingsWindow:
    API_FACTORY=None
    TITLE='VGN S99'
    PROFILE_MODEL='VGN S99 2.4G'
    MACRO_FILE='s99-macros.json'
    MAX_MACROS=32
    MAX_MACRO_NAME=40
    SUBTITLE='灯效、按键和性能，一处设置。'

    def __init__(self, root, on_close=None):
        self.api = self.API_FACTORY() if self.API_FACTORY else S99Settings()
        self.window = tk.Toplevel(root)
        self.window.title(self.TITLE+' · 设备设置')
        self.window.geometry('790x690')
        self.window.minsize(740, 640)
        self.window.configure(bg=BG)
        self.window.transient(root)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>',lambda _:self.close())
        self._style()
        self.on_close = on_close
        self.busy = False
        self.closed = False
        self.results = queue.Queue()
        self.snapshot = None
        self.keys_snapshot = None
        self.macros = []
        self.installed_macros = None
        self.variables = {}
        self.original_values = {}
        self.local_path = ROOT/'runtime'/'config'/self.MACRO_FILE
        top = tk.Frame(self.window, bg=BG, padx=24, pady=14)
        top.pack(fill='x')
        self.header_art=device_image(top,'keyboard',self.TITLE,compact=True)
        tk.Label(top,image=self.header_art,bg=BG,bd=0).pack(side='right')
        heading=tk.Frame(top,bg=BG)
        heading.pack(side='left')
        tk.Label(heading, text=self.TITLE, bg=BG, fg=TEXT, font=('Microsoft YaHei UI',20,'bold')).pack(anchor='w')
        tk.Label(heading, text=self.SUBTITLE, bg=BG, fg=MUTED).pack(anchor='w', pady=(4,0))
        self.tabs = SegmentedNotebook(self.window)
        self.tabs.pack(fill='both', expand=True, padx=24)
        self.light_page = self.page('灯效与性能')
        self.key_page = self.page('按键映射')
        self.macro_page = self.page('宏')
        self.profile_page = self.page('配置与备份')
        self._build_settings()
        self._build_keys()
        self._build_macros()
        self._build_profiles()
        self.status = tk.Label(self.window, text='正在读取当前设置…', bg=BG, fg=MUTED,
                               anchor='w', justify='left', wraplength=735, padx=24, pady=14)
        self.status.bind('<Configure>',lambda e:self.status.configure(wraplength=max(200,e.width-48)))
        # Reserve feedback space before allocating the expandable page. This
        # keeps errors and transfer status visible even at the minimum size.
        self.status.pack(side='bottom',fill='x',before=self.tabs.nav)
        self.timer = self.window.after(100, self.poll)
        try:
            if self.local_path.exists():
                self.macros = json.loads(self.local_path.read_text(encoding='utf-8'))
                if not isinstance(self.macros,list):raise ValueError('宏库格式错误')
                if self.macros: self.validate_macros(self.macros)
        except (OSError, ValueError, TypeError):
            self.macros = []
            self.set_status('本地宏库无法读取，原文件已保留。', error=True)
        self.refresh_macro_list()
        self.read_settings()

    def _style(self):
        style_controls(self.window)

    def page(self, name):
        page = tk.Frame(self.tabs, bg=SURFACE, padx=20, pady=18)
        self.tabs.add(page, text=name)
        return page

    def label(self, parent, text):
        label=tk.Label(parent,text=text,bg=SURFACE,fg=MUTED,justify='left',anchor='w',wraplength=675)
        label.pack(fill='x',pady=(0,10))
        label.bind('<Configure>',lambda e:label.configure(wraplength=max(180,e.width)))

    def button(self, parent, text, command, **kwargs):
        width=Font(root=self.window,family='Microsoft YaHei UI',size=10).measure(text)+30
        return PillButton(parent,text,command,width=width,height=36,bg=SURFACE,
                          primary=text.startswith(('应用','写入')),**kwargs)

    def set_status(self, text, error=False):
        self.status.configure(text=text, fg=RED if error else MUTED)

    def run(self, action, done, text):
        if self.busy:
            self.set_status('上一项操作尚未结束，请稍候。')
            return
        self.busy = True
        self.set_status(text)
        def worker():
            try: self.results.put((True, action(), done))
            except Exception as exc: self.results.put((False, str(exc), None))
        threading.Thread(target=worker, daemon=True, name='S99-settings').start()

    def poll(self):
        if self.closed: return
        try:
            ok, result, done = self.results.get_nowait()
        except queue.Empty: pass
        else:
            self.busy = False
            if ok:
                try: done(result)
                except Exception as exc: self.set_status(str(exc), error=True)
            else: self.set_status(result, error=True)
        self.timer = self.window.after(100, self.poll)

    def close(self):
        if self.busy:
            self.set_status('正在与设备通信，完成后即可关闭。')
            return False
        self.closed = True
        self.window.after_cancel(self.timer)
        self.window.destroy()
        if self.on_close: self.on_close()
        return True

    def _build_settings(self):
        p = self.light_page
        self.label(p, '先读取设备设置，修改后点击应用。只写入你改动的选项。')
        form = tk.Frame(p, bg=SURFACE)
        form.pack(fill='x')
        self.choices = {
            'mode': {v:k for k,v in LIGHT_MODES.items()},
            'brightness': {str(i):i for i in range(5)},
            'speed': {str(i):4-i for i in range(5)},
            'direction': {'方向一':0, '方向二':1},
            'rainbow': {'单色':0, '多彩':1},
            'polling': {'1000 Hz':0,'500 Hz':1,'250 Hz':2,'125 Hz':3},
            'sleep': {'1 分钟':60,'3 分钟':180,'5 分钟':300,'10 分钟':600,'30 分钟':1800,'不休眠':65535},
        }
        titles = [('mode','灯效'),('brightness','亮度（0–4）'),('speed','速度（0–4）'),
                  ('direction','方向'),('rainbow','色彩'),('polling','回报率'),('sleep','休眠时间')]
        for row, (name, title) in enumerate(titles):
            tk.Label(form, text=title, bg=SURFACE, fg=TEXT, width=18, anchor='w').grid(row=row,column=0,sticky='w',pady=5)
            var = tk.StringVar()
            self.variables[name] = var
            ttk.Combobox(form, textvariable=var, values=list(self.choices[name]), state='readonly', width=24).grid(row=row,column=1,sticky='w',pady=2)
        self.color = tk.StringVar(value='#EE00F1')
        ttk.Entry(form, textvariable=self.color, width=16).grid(row=4,column=2,padx=(16,6))
        self.button(form,'选颜色',self.choose_color).grid(row=4,column=3)
        flags = tk.Frame(p,bg=SURFACE)
        flags.pack(fill='x', pady=10)
        for i,(name,title) in enumerate([('win_lock','锁定 Win 键'),('six_key','六键模式'),('wasd_swap','WASD 与方向键互换'),('mac_mode','Mac 模式')]):
            var = tk.BooleanVar()
            self.variables[name] = var
            ttk.Checkbutton(flags, text=title, variable=var).grid(row=i//2,column=i%2,sticky='w',padx=(0,30))
        actions = tk.Frame(p,bg=SURFACE)
        actions.pack(fill='x',pady=(6,0))
        self.button(actions,'重新读取',self.read_settings).pack(side='left')
        self.button(actions,'应用灯效与性能',self.apply_settings).pack(side='right')

    def choose_color(self):
        _, value = colorchooser.askcolor(self.color.get(),parent=self.window)
        if value:
            self.color.set(value.upper())
            self.variables['rainbow'].set('单色')

    def read_settings(self):
        self.run(self.api.read_settings,self.show_settings,'正在读取 '+self.TITLE+' 配置；若超时，请按键唤醒。')

    def validate_macros(self,macros):return encode_macros(macros)

    def validate_changes(self,changes):return patch_settings(self.snapshot['raw'],changes)

    def target_options(self):return ['恢复此键默认','禁用 / Fn 层继承']+list(KEYS)

    def show_settings(self, result):
        self.snapshot = result
        self.original_values = result['values'].copy()
        self.set_form(result['values'])
        suffix = ' 已备份并回读核验。' if result.get('backup') else ''
        self.set_status('已读取当前灯效与性能设置。'+suffix)

    def set_form(self, values):
        for name,var in self.variables.items():
            value=values[name]
            if name in self.choices:
                var.set(next((k for k,v in self.choices[name].items() if v==value), f'设备值 {value}（保持）'))
            else: var.set(value)
        self.color.set('#{:02X}{:02X}{:02X}'.format(values['red'],values['green'],values['blue']))

    def collect_settings(self):
        if not self.snapshot: raise ValueError('请先成功读取设备设置')
        values = self.original_values.copy()
        for name,var in self.variables.items():
            if name in self.choices:
                values[name] = self.choices[name].get(var.get(), self.original_values[name])
            elif name == 'six_key': values[name] = bool(var.get())
            else: values[name] = int(var.get())
        color = self.color.get().strip()
        if len(color)!=7 or not color.startswith('#'): raise ValueError('颜色格式为 #RRGGBB')
        rgb = bytes.fromhex(color[1:])
        if len(rgb)!=3: raise ValueError('颜色格式为 #RRGGBB')
        values.update(zip(('red','green','blue'),rgb))
        return values

    def apply_settings(self):
        try:
            values=self.collect_settings()
            changes={k:v for k,v in values.items() if v!=self.original_values[k]}
            self.validate_changes(changes)
        except ValueError as exc: self.set_status(str(exc),True); return
        if not changes: self.set_status('没有需要应用的修改。'); return
        expected=self.snapshot
        self.run(lambda:self.api.apply_settings(changes,expected),self.show_settings,'正在备份、应用并回读核验…')

    def _build_keys(self):
        p=self.key_page
        self.label(p,'读取后选择一个按键，设置普通层或 Fn 层。特殊键保留设备原值。')
        tree_box=tk.Frame(p,bg=SURFACE);tree_box.pack(fill='both',expand=True)
        self.key_tree=ttk.Treeview(tree_box,columns=('key','normal','fn'),show='headings',height=10,selectmode='browse',style='S99.Treeview')
        for col,title in [('key','原始按键'),('normal','普通层'),('fn','Fn 层')]:
            self.key_tree.heading(col,text=title);self.key_tree.column(col,width=205)
        scrollbar=ttk.Scrollbar(tree_box,orient='vertical',command=self.key_tree.yview)
        self.key_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y');self.key_tree.pack(side='left',fill='both',expand=True)
        self.key_tree.bind('<<TreeviewSelect>>',lambda _:self.select_key())
        form=tk.Frame(p,bg=SURFACE);form.pack(side='bottom',fill='x',pady=(12,0),before=tree_box)
        self.layer=tk.StringVar(value='普通层')
        self.target=tk.StringVar(value='A')
        ttk.Combobox(form,textvariable=self.layer,values=['普通层','Fn 层'],state='readonly',width=12).pack(side='left')
        self.target_box=ttk.Combobox(form,textvariable=self.target,values=['恢复此键默认','禁用 / Fn 层继承']+list(KEYS),state='readonly',width=32)
        self.target_box.pack(side='left',padx=12)
        self.button(form,'应用此键',self.apply_key).pack(side='right')
        self.button(p,'读取按键映射',self.read_keys).pack(side='bottom',anchor='w',before=tree_box)

    def read_keys(self):
        self.run(self.api.read_keys,self.show_keys,'正在读取普通层和 Fn 层映射…')

    def show_keys(self,result):
        self.keys_snapshot=result
        selected=self.key_tree.selection()
        self.key_tree.delete(*self.key_tree.get_children())
        for idx in range(len(result['default'])//3):
            values=[result[k][idx*3:idx*3+3] for k in ('default','normal','fn')]
            if any(v != b'\0\0\0' for v in values) and values[0] != bytes((32,0,0)):
                self.key_tree.insert('', 'end',iid=str(idx),values=[key_name(v) for v in values])
        if selected and self.key_tree.exists(selected[0]):self.key_tree.selection_set(selected)
        self.set_status('按键映射已读取。'+(' 修改已回读核验。' if result.get('backup') else ''))

    def select_key(self):
        selected=self.key_tree.selection()
        if selected:self.set_status('已选择 '+self.key_tree.item(selected[0],'values')[0]+'；选择目标功能后点击应用。')

    def apply_key(self):
        selected=self.key_tree.selection()
        if not selected or not self.keys_snapshot:self.set_status('请先读取并选择按键。',True);return
        idx=int(selected[0]);layer='normal' if self.layer.get()=='普通层' else 'fn'
        target=self.target.get()
        if target=='恢复此键默认':value=self.keys_snapshot['default'][idx*3:idx*3+3] if layer=='normal' else bytes(3)
        elif target=='禁用 / Fn 层继承':value=bytes(3)
        elif target.startswith('宏 · '):
            if not self.installed_macros:self.set_status('请先写入宏库。',True);return
            name=target[4:]
            names=[m['name'] for m in self.installed_macros]
            if name not in names:self.set_status('宏库已变化，请重新写入。',True);return
            value=(113,names.index(name),1)
        elif target in KEYS:value=(32,*KEYS[target])
        else:self.set_status('请选择一个有效功能。',True);return
        expected=self.keys_snapshot
        self.run(lambda:self.api.apply_key(idx,value,layer,expected),self.show_keys,'正在备份并应用按键映射…')

    def _build_macros(self):
        p=self.macro_page
        self.label(p,'编辑按下、释放与延迟；保存到本机后可导出。写入设备后，在“按键映射”绑定宏（执行一次）。')
        top=tk.Frame(p,bg=SURFACE);top.pack(fill='x')
        self.macro_name=tk.StringVar()
        self.macro_combo=ttk.Combobox(top,textvariable=self.macro_name,width=27)
        self.macro_combo.pack(side='left');self.macro_combo.bind('<<ComboboxSelected>>',lambda _:self.show_macro())
        self.button(top,'新建宏',self.new_macro).pack(side='left',padx=8)
        self.button(top,'重命名',self.rename_macro).pack(side='left',padx=(0,8))
        self.button(top,'删除宏',self.delete_macro).pack(side='left')
        tree_box=tk.Frame(p,bg=SURFACE);tree_box.pack(fill='both',expand=True,pady=12)
        self.event_tree=ttk.Treeview(tree_box,columns=('action','key','delay'),show='headings',height=6,selectmode='browse',style='S99.Treeview')
        for col,title in [('action','动作'),('key','按键'),('delay','延迟（毫秒）')]:self.event_tree.heading(col,text=title);self.event_tree.column(col,width=180)
        scrollbar=ttk.Scrollbar(tree_box,orient='vertical',command=self.event_tree.yview)
        self.event_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y');self.event_tree.pack(side='left',fill='both',expand=True)
        footer=tk.Frame(p,bg=SURFACE);footer.pack(side='bottom',fill='x',before=tree_box)
        add=tk.Frame(footer,bg=SURFACE);add.pack(fill='x')
        self.event_action=tk.StringVar(value='按下');self.event_key=tk.StringVar(value='A');self.event_delay=tk.StringVar(value='50')
        ttk.Combobox(add,textvariable=self.event_action,values=['按下','释放'],state='readonly',width=7).pack(side='left')
        ttk.Combobox(add,textvariable=self.event_key,values=list(KEYS),state='readonly',width=17).pack(side='left',padx=6)
        ttk.Spinbox(add,textvariable=self.event_delay,from_=0,to=65535,width=7).pack(side='left')
        self.button(add,'添加动作',self.add_event).pack(side='left',padx=6)
        self.button(add,'删除动作',self.remove_event).pack(side='left')
        self.replace_macros=tk.BooleanVar(value=False)
        ttk.Checkbutton(footer,text='已导出需要保留的旧宏，允许覆盖设备宏库',variable=self.replace_macros).pack(anchor='w',pady=(14,4))
        self.label(footer,'设备接口无法完整备份旧宏内容。写入后核对设备应答，实际触发需自行验证。')
        buttons=tk.Frame(footer,bg=SURFACE);buttons.pack(fill='x')
        self.button(buttons,'保存到本机',self.save_macros).pack(side='left')
        self.button(buttons,'写入设备宏库',self.install_macros).pack(side='right')
        self.active_macro=None

    def refresh_macro_list(self):
        self.macro_combo.configure(values=[m['name'] for m in self.macros])
        if self.macros:
            self.macro_name.set(self.macros[0]['name']);self.show_macro()
        else:
            self.macro_name.set('');self.active_macro=None;self.event_tree.delete(*self.event_tree.get_children())

    def show_macro(self):
        self.active_macro=next((m for m in self.macros if m['name']==self.macro_name.get()),None)
        self.event_tree.delete(*self.event_tree.get_children())
        if self.active_macro:
            for i,e in enumerate(self.active_macro['events']):
                value=(32,e['key'],0) if e['type']==9 else (32,0,e['key'])
                label=key_name(value) if e['type'] in (9,10) else '鼠标键 '+str(e['key'])
                self.event_tree.insert('','end',iid=str(i),values=('按下' if e['action']=='down' else '释放',label,e['delay']))

    def new_macro(self):
        name=self.macro_name.get().strip()
        names={m['name'] for m in self.macros}
        if not name or name in names:
            index=1
            while f'新宏 {index}' in names:index+=1
            name=f'新宏 {index}'
        if len(name)>self.MAX_MACRO_NAME:self.set_status(f'宏名称最长 {self.MAX_MACRO_NAME} 个字符。',True);return
        if len(self.macros)>=self.MAX_MACROS:self.set_status(f'最多支持 {self.MAX_MACROS} 个宏。',True);return
        self.macros.append({'name':name,'events':[]})
        self.macro_name.set(name)
        self.macro_combo.configure(values=[m['name'] for m in self.macros]);self.show_macro()
        self.set_status('宏已建立；添加成对的按下与释放动作。')

    def rename_macro(self):
        name=self.macro_name.get().strip()
        if not self.active_macro or not 1<=len(name)<=self.MAX_MACRO_NAME:
            self.set_status(f'先选中宏，再输入 1–{self.MAX_MACRO_NAME} 个字符的新名称。',True);return
        if any(m is not self.active_macro and m['name']==name for m in self.macros):
            self.set_status('宏名称已存在。',True);return
        self.active_macro['name']=name
        self.macro_combo.configure(values=[m['name'] for m in self.macros])
        self.show_macro()

    def delete_macro(self):
        if self.active_macro:self.macros.remove(self.active_macro)
        self.refresh_macro_list()

    def add_event(self):
        if not self.active_macro:self.set_status('请先输入名称并新建宏。',True);return
        try:
            mod,key=KEYS[self.event_key.get()];delay=int(self.event_delay.get())
            if not 0<=delay<=65535:raise ValueError()
        except (KeyError,ValueError):self.set_status('请选择按键，并填写 0–65535 毫秒延迟。',True);return
        self.active_macro['events'].append({'type':9 if mod else 10,'key':mod or key,'action':'down' if self.event_action.get()=='按下' else 'up','delay':delay})
        self.show_macro()

    def remove_event(self):
        selected=self.event_tree.selection()
        if self.active_macro and selected:del self.active_macro['events'][int(selected[0])];self.show_macro()

    def save_macros(self):
        try:
            if self.macros:self.validate_macros(self.macros)
            self.local_path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.local_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.macros,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.local_path)
            self.set_status('宏库已保存到本机；尚未写入设备。')
        except (ValueError,OSError) as exc:self.set_status(str(exc),True)

    def install_macros(self):
        if self.busy:self.set_status('请等待当前操作完成。');return
        try:self.validate_macros(self.macros)
        except ValueError as exc:self.set_status(str(exc),True);return
        if not self.replace_macros.get():self.set_status('请先导出旧宏，并勾选允许覆盖设备宏库。',True);return
        macros=json.loads(json.dumps(self.macros))
        # A partially failed replacement must not leave old bindings advertised.
        self.installed_macros=None
        self.target_box.configure(values=self.target_options())
        def done(result):
            self.installed_macros=macros
            self.target_box.configure(values=self.target_options()+['宏 · '+m['name'] for m in macros])
            self.replace_macros.set(False)
            self.set_status(result['status']+'。请读取按键映射后绑定宏。')
        self.run(lambda:self.api.install_macros(macros,replace_confirmed=True),done,'正在写入宏库，请勿拔除接收器…')

    def _build_profiles(self):
        p=self.profile_page
        self.label(p,'导出灯效、性能选项和本地宏库。导入只填入编辑器，需分别点击应用或写入设备。按键映射的原始备份可单独恢复。')
        self.button(p,'导出配置文件…',self.export_profile).pack(anchor='w',pady=7)
        self.button(p,'导入配置文件…',self.import_profile).pack(anchor='w',pady=7)
        self.button(p,'打开自动备份文件夹',self.open_backups).pack(anchor='w',pady=7)
        self.button(p,'恢复设置 / 改键备份…',self.restore_backup).pack(anchor='w',pady=7)
        self.label(p,'当前支持本机 S99 的 2.4G 连接。固件升级、音乐律动和其他型号的专属功能继续通过 V HUB 使用。')
        self.button(p,'打开 V HUB',self.open_vendor).pack(anchor='w',pady=7)
        self.label(p,'打开 V HUB 后请关闭此设置窗口，避免同时修改设备。')

    def export_profile(self):
        try:
            values=self.collect_settings()
            if self.macros:self.validate_macros(self.macros)
            path=filedialog.asksaveasfilename(parent=self.window,initialdir=str(ROOT/'runtime'/'config'),defaultextension='.json',initialfile=self.TITLE+'-profile.json',filetypes=[('设备配置','*.json')])
            if not path:return
            Path(path).write_text(json.dumps({'schema':1,'model':self.PROFILE_MODEL,'settings':values,'macros':self.macros},ensure_ascii=False,indent=2),encoding='utf-8')
            self.set_status('配置已导出。')
        except (OSError,ValueError) as exc:self.set_status(str(exc),True)

    def import_profile(self):
        if self.busy:self.set_status('请等待当前操作完成后再导入。');return
        if not self.snapshot:self.set_status('请先读取当前设备配置，再导入。',True);return
        path=filedialog.askopenfilename(parent=self.window,initialdir=str(ROOT/'runtime'/'config'),filetypes=[('设备配置','*.json')])
        if not path:return
        try:
            if Path(path).stat().st_size>100_000:raise ValueError('配置文件过大')
            data=json.loads(Path(path).read_text(encoding='utf-8'))
            if not isinstance(data,dict):raise ValueError('配置必须为对象')
            if data.get('schema')!=1 or data.get('model')!=self.PROFILE_MODEL:raise ValueError('不是兼容的设备配置')
            values=data['settings'];macros=data['macros']
            if set(values)!=set(self.original_values):raise ValueError('配置字段不完整')
            changes={k:v for k,v in values.items() if v!=self.original_values[k]}
            self.validate_changes(changes)
            if not isinstance(macros,list):raise ValueError('宏库格式不正确')
            if macros:self.validate_macros(macros)
            self.set_form(values);self.macros=macros;self.refresh_macro_list()
            self.set_status('已导入编辑器；尚未修改设备。')
        except (OSError,ValueError,KeyError,TypeError) as exc:self.set_status('导入失败：'+str(exc),True)

    def open_backups(self):
        folder=ROOT/'runtime'/'backups'/'s99';folder.mkdir(parents=True,exist_ok=True);os.startfile(folder)

    def restore_backup(self):
        if self.busy:self.set_status('请等待当前操作完成。');return
        path=filedialog.askopenfilename(parent=self.window,initialdir=str(ROOT/'runtime'/'backups'/'s99'),filetypes=[('S99 备份','*.json')])
        if path and messagebox.askyesno('恢复设备配置','将用所选备份恢复对应设置或按键映射。继续？',parent=self.window):
            def done(result):
                self.snapshot=None;self.keys_snapshot=None
                self.set_status('恢复完成，已回读核验；请重新读取设置和按键。')
            self.run(lambda:self.api.restore_backup(path),done,'正在备份当前配置并恢复…')

    def open_vendor(self):
        if self.busy:self.set_status('请等待当前操作完成。');return
        path=Path(r'E:\VGN\V HUB\V HUB.exe')
        if path.is_file():os.startfile(path)
        else:self.set_status('未找到本机 V HUB。',True)
