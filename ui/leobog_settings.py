"""Alice 75 adapter using the shared asynchronous settings/editor shell."""
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk,colorchooser,filedialog,messagebox

from config.settings import ROOT
from providers.leobog_settings import (Alice75Settings,MODEL,MODES,decode_settings,
    patch_settings,encode_macros,keyboard_value,macro_value)
from ui.s99_settings import S99SettingsWindow,KEYS
from ui.widgets import BG,SURFACE,TEXT,MUTED

MEDIA={'播放 / 暂停':bytes.fromhex('02 00 00 cd'),'静音':bytes.fromhex('02 00 00 e2'),
       '音量加':bytes.fromhex('02 00 00 e9'),'音量减':bytes.fromhex('02 00 00 ea'),
       '上一曲':bytes.fromhex('02 00 00 b6'),'下一曲':bytes.fromhex('02 00 00 b5')}
LAYERS={'默认层':0,'FN1 层':1,'FN2 层':2,'第 4 配置层':3}

def function_name(value):
    if value==bytes(4):return '禁用 / 空白'
    if value[0]==0:
        mod,second,key=value[1:]
        if key>=0xe0:
            mod|=1<<(key-0xe0) if key<=0xe7 else 0;key=0
        names=[name for name,(mask,_) in KEYS.items() if mask and mask&mod]
        for code in (key,second):
            if code:names.append(next((name for name,(mask,hid) in KEYS.items() if not mask and hid==code),f'按键 {code}'))
        return ' + '.join(names) or '空白'
    for name,raw in MEDIA.items():
        if raw==value:return name
    if value[0]==0xd:return 'Fn' if value[1]==0 else 'Fn2'
    if value[0]==3:return f'宏 {value[3]+1}'
    return '设备特殊功能'

class Alice75SettingsWindow(S99SettingsWindow):
    API_FACTORY=Alice75Settings
    TITLE='LEOBOG Alice 75'
    PROFILE_MODEL='LEOBOG Alice 75'
    MACRO_FILE='alice75-macros.json'
    MAX_MACROS=16
    MAX_MACRO_NAME=30
    SUBTITLE='灯效、按键与宏，一处设置。'

    def validate_macros(self,macros):return encode_macros(macros)

    def validate_changes(self,changes):
        mode=MODES.get(changes.get('mode',self.original_values['mode']),{})
        # Profiles may contain null for controls that the selected effect lacks.
        cleaned={k:v for k,v in changes.items() if not (v is None and k not in ('mode','sleep') and not mode.get('color' if k in ('red','green','blue') else k))}
        return patch_settings(self.snapshot['raw'],self.snapshot['palette'],cleaned)

    def target_options(self):return ['恢复本次读取的映射','禁用','Fn','Fn2']+list(KEYS)+list(MEDIA)

    def _build_settings(self):
        p=self.light_page
        self.tabs.tab(p,text='灯效与休眠')
        self.label(p,'协议已按本机 LEOBOG ONE 移植，设置读写尚待真机验证。成功读取后才能应用。')
        self.choices={
            'mode':{m['name']:code for code,m in MODES.items()},
            'brightness':{str(i):i for i in range(5)},'speed':{str(i):i for i in range(5)},
            'rainbow':{'单色':0,'多彩':7},
            'sleep':{'不休眠':0,'30 秒':30,'1 分钟':60,'90 秒':90,'2 分钟':120,'3 分钟':180,
                     '4 分钟':240,'5 分钟':300,'10 分钟':600,'15 分钟':900,'20 分钟':1200}}
        self.controls={}
        form=tk.Frame(p,bg=SURFACE);form.pack(fill='x')
        for row,(name,title) in enumerate([('mode','灯效'),('brightness','亮度（0–4）'),('speed','速度（0–4）'),('rainbow','色彩'),('sleep','休眠时间')]):
            tk.Label(form,text=title,bg=SURFACE,fg=TEXT,width=18,anchor='w').grid(row=row,column=0,sticky='w',pady=8)
            var=tk.StringVar();self.variables[name]=var
            self.controls[name]=ttk.Combobox(form,textvariable=var,values=list(self.choices[name]),state='readonly',width=25)
            self.controls[name].grid(row=row,column=1,sticky='w')
        self.controls['mode'].bind('<<ComboboxSelected>>',lambda _:self.mode_changed())
        self.color=tk.StringVar(value='#FFFFFF')
        self.color_entry=ttk.Entry(form,textvariable=self.color,width=16)
        self.color_entry.grid(row=5,column=1,sticky='w',pady=8)
        tk.Label(form,text='灯光颜色',bg=SURFACE,fg=TEXT,anchor='w').grid(row=5,column=0,sticky='w')
        self.color_button=self.button(form,'选择颜色',self.choose_color)
        self.color_button.grid(row=5,column=2,padx=12)
        self.label(p,'“自定义模式”的每键灯色在按键页编辑。亮度与速度按当前灯效分别保存。')
        buttons=tk.Frame(p,bg=SURFACE);buttons.pack(fill='x',pady=22)
        self.button(buttons,'重新读取',self.read_settings).pack(side='left')
        self.button(buttons,'应用灯效与休眠',self.apply_settings).pack(side='right')

    def show_settings(self,result):
        self.snapshot=result;self.original_values=result['values'].copy();self.set_form(result['values'])
        self.set_status('已读取 Alice 75 设置。'+('已备份并回读核验。' if result.get('backup') else ''))

    def set_form(self,values):
        for name,var in self.variables.items():
            value=values[name]
            var.set(next((title for title,data in self.choices[name].items() if data==value),f'保持设备值 {value}' if value is not None else '此灯效不支持'))
        rgb=[values[x] for x in ('red','green','blue')]
        self.color.set('#{:02X}{:02X}{:02X}'.format(*rgb) if all(x is not None for x in rgb) else '')
        self.update_capabilities(values['mode'])

    def update_capabilities(self,code):
        mode=MODES.get(code,{})
        for name in ('brightness','speed','rainbow'):
            self.controls[name].configure(state='readonly' if mode.get(name) else 'disabled')
        for control in (self.color_entry,self.color_button):
            control.configure(state='normal' if mode.get('color') else 'disabled')

    def mode_changed(self):
        code=self.choices['mode'].get(self.variables['mode'].get())
        if not self.snapshot:self.update_capabilities(code);return
        raw=bytearray(self.snapshot['raw']);raw[10]=code
        values=decode_settings(raw,self.snapshot['palette'])
        # Keep a user's edited sleep time when they browse lighting effects.
        values['sleep']=self.choices['sleep'].get(self.variables['sleep'].get(),self.original_values['sleep'])
        self.set_form(values)

    def choose_color(self):
        _,color=colorchooser.askcolor(self.color.get() or '#FFFFFF',parent=self.window)
        if color:
            self.color.set(color.upper())
            self.variables['rainbow'].set('单色')

    def collect_settings(self):
        if not self.snapshot:raise ValueError('请先成功读取 Alice 75 设置')
        values=self.original_values.copy()
        for name,var in self.variables.items():
            values[name]=self.choices[name].get(var.get(),values[name])
        mode=MODES.get(values['mode'],{})
        if mode.get('color'):
            color=self.color.get().strip()
            if len(color)!=7 or not color.startswith('#'):raise ValueError('颜色格式为 #RRGGBB')
            rgb=bytes.fromhex(color[1:])
            if len(rgb)!=3:raise ValueError('颜色格式为 #RRGGBB')
            values.update(zip(('red','green','blue'),rgb))
        # Unsupported parameters are omitted from the diff, preserving their bytes.
        for name in ('brightness','speed','rainbow'):
            if not mode.get(name):values[name]=self.original_values[name]
        if not mode.get('color'):
            for name in ('red','green','blue'):values[name]=self.original_values[name]
        return values

    def _build_keys(self):
        p=self.key_page
        self.label(p,'按官方 Alice 布局选择按键。选择配置层后先读取，再应用目标功能；支持修饰键组合。')
        form=tk.Frame(p,bg=SURFACE);form.pack(fill='x',pady=(0,12))
        self.layer=tk.StringVar(value='默认层')
        ttk.Combobox(form,textvariable=self.layer,values=list(LAYERS),state='readonly',width=17).pack(side='left')
        self.button(form,'读取此层映射',self.read_keys).pack(side='left',padx=10)
        self.read_layer=tk.Label(form,text='',bg=SURFACE,fg=MUTED);self.read_layer.pack(side='right')
        tree_box=tk.Frame(p,bg=SURFACE);tree_box.pack(fill='both',expand=True)
        self.key_tree=ttk.Treeview(tree_box,columns=('key','value'),show='headings',height=8,selectmode='browse',style='S99.Treeview')
        for col,title in [('key','Alice 75 按键'),('value','当前功能')]:
            self.key_tree.heading(col,text=title);self.key_tree.column(col,width=300)
        bar=ttk.Scrollbar(tree_box,orient='vertical',command=self.key_tree.yview)
        self.key_tree.configure(yscrollcommand=bar.set);bar.pack(side='right',fill='y');self.key_tree.pack(side='left',fill='both',expand=True)
        bottom=tk.Frame(p,bg=SURFACE);bottom.pack(side='bottom',fill='x',pady=(12,8),before=tree_box)
        self.target=tk.StringVar(value='A')
        self.target_box=ttk.Combobox(bottom,textvariable=self.target,values=self.target_options(),state='readonly',width=29)
        self.target_box.pack(side='left')
        self.button(bottom,'应用此键',self.apply_key).pack(side='right')
        flags=tk.Frame(p,bg=SURFACE);flags.pack(side='bottom',fill='x',before=bottom)
        self.modifiers={}
        for name in ('Left Ctrl','Left Shift','Left Alt','Left Win'):
            var=tk.BooleanVar();self.modifiers[name]=var
            ttk.Checkbutton(flags,text=name.replace('Left ',''),variable=var).pack(side='left')
        self.button(flags,'设置此键灯色…',self.apply_custom_color).pack(side='right')
        self.initial_layers={}

    def read_keys(self):
        layer=LAYERS[self.layer.get()]
        self.run(lambda:self.api.read_keys(layer),self.show_keys,'正在读取 Alice 75 按键映射…')

    def show_keys(self,result):
        self.keys_snapshot=result
        self.initial_layers.setdefault((result['receiver'],result['layer']),result['raw'])
        self.key_tree.delete(*self.key_tree.get_children())
        for key in MODEL['keys']:
            slot=key['slot'];self.key_tree.insert('','end',iid=str(slot),values=(key['name'],function_name(result['raw'][slot*4:slot*4+4])))
        title=next(title for title,number in LAYERS.items() if number==result['layer'])
        self.read_layer.configure(text='当前显示：'+title)
        self.set_status('已读取 '+title+'。'+('修改已回读核验。' if result.get('backup') else ''))

    def apply_key(self):
        selected=self.key_tree.selection()
        if not selected or not self.keys_snapshot:self.set_status('请先读取并选择一个按键。',True);return
        if LAYERS[self.layer.get()]!=self.keys_snapshot['layer']:self.set_status('切换配置层后请先读取映射。',True);return
        slot=int(selected[0]);target=self.target.get();expected=self.keys_snapshot
        if target=='恢复本次读取的映射':value=self.initial_layers[(expected['receiver'],expected['layer'])][slot*4:slot*4+4]
        elif target=='禁用':value=bytes(4)
        elif target in ('Fn','Fn2'):value=bytes((13,int(target=='Fn2'),0,0))
        elif target in MEDIA:value=MEDIA[target]
        elif target.startswith('宏 · '):
            names=[m['name'] for m in self.installed_macros or []]
            if target[4:] not in names:self.set_status('请先写入对应宏库。',True);return
            value=macro_value(names.index(target[4:]))
        elif target in KEYS:
            modifier,hid=KEYS[target]
            for name,var in self.modifiers.items():
                if var.get():modifier|=KEYS[name][0]
            value=keyboard_value(hid,modifier)
        else:self.set_status('请选择有效功能。',True);return
        self.run(lambda:self.api.apply_key(slot,value,expected),self.show_keys,'正在备份并应用 Alice 75 按键…')

    def apply_custom_color(self):
        selected=self.key_tree.selection()
        if not selected or not self.keys_snapshot:self.set_status('请先读取并选择按键。',True);return
        _,color=colorchooser.askcolor(parent=self.window)
        if not color:return
        slot=int(selected[0]);rgb=tuple(bytes.fromhex(color[1:]));token=self.keys_snapshot['receiver']
        self.run(lambda:self.api.apply_custom_color(slot,rgb,token),lambda _:self.set_status('单键灯色已回读核验；选择并应用“自定义模式”后可见。'),'正在备份并应用单键灯色…')

    def _build_profiles(self):
        p=self.profile_page
        self.label(p,'导入导出灯效、休眠选项和本地宏库；导入只填入编辑器，不立即写入键盘。')
        self.button(p,'导出配置文件…',self.export_profile).pack(anchor='w',pady=7)
        self.button(p,'导入配置文件…',self.import_profile).pack(anchor='w',pady=7)
        self.button(p,'打开自动备份文件夹',self.open_backups).pack(anchor='w',pady=7)
        self.button(p,'恢复设置 / 改键备份…',self.restore_backup).pack(anchor='w',pady=7)
        self.label(p,'设置读写、改键、单键灯色与宏写入已实现，当前接收器缺席，尚未完成真机验收。固件升级、声音律动继续使用原驱动。')
        self.button(p,'打开 LEOBOG ONE',self.open_vendor).pack(anchor='w',pady=7)
        self.label(p,'型号共用接收器 PID；本工具仅接受此前已确认的 Alice 75 接收器和 USB 端口。')

    def open_backups(self):
        folder=ROOT/'runtime/backups/alice75';folder.mkdir(parents=True,exist_ok=True);os.startfile(folder)

    def restore_backup(self):
        if self.busy:self.set_status('请等待当前操作完成。');return
        path=filedialog.askopenfilename(parent=self.window,initialdir=str(ROOT/'runtime/backups/alice75'),filetypes=[('Alice 75 备份','*.json')])
        if path and messagebox.askyesno('恢复 Alice 75 配置','将按所选备份恢复对应配置。继续？',parent=self.window):
            def done(_):
                self.snapshot=None;self.keys_snapshot=None;self.initial_layers.clear()
                self.set_status('恢复完成并回读核验；请重新读取设置与映射。')
            self.run(lambda:self.api.restore_backup(path),done,'正在备份当前配置并恢复…')

    def open_vendor(self):
        if self.busy:self.set_status('请等待当前操作完成。');return
        path=Path(r'E:\VGN\LEOBOG ONE\OemDrv.exe')
        if path.is_file():os.startfile(path)
        else:self.set_status('未找到本机 LEOBOG ONE。',True)
