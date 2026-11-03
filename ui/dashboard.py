import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox
from providers.base import BatteryState
from controller import Controller, LowBatteryGate
from config import settings,autostart
from ui.tray import Tray
from ui.cards import DeviceCard
from ui.widgets import (BG,SURFACE,TEXT,MUTED,BORDER,GREEN,AMBER,RED,
                        PillButton,RoundedPanel,Switch,style_controls)


class Dashboard:
    def __init__(self,root,providers,hidden=False):
        self.root = root
        self.settings,self.config_error = settings.load()
        self.events = queue.Queue()
        self.states = {}
        self.quitting = False
        self.gate = LowBatteryGate()
        self.provider_states = [p.last_state for p in providers]
        self.settings_dialog = None
        self.s99_dialog = None
        self.alice75_dialog = None
        self.gpw_dialog = None
        self.root.title('无线设备电量')
        height=min(760,max(560,self.root.winfo_screenheight()-100))
        self.root.geometry(f'900x{height}')
        self.root.minsize(800,560)
        self.root.configure(bg=BG)
        self.root.option_add('*Font',('Microsoft YaHei UI',10))
        self.root.protocol('WM_DELETE_WINDOW',self.on_close)
        self._style()
        self._build()
        self.root.bind('<Control-r>',lambda _:self.refresh())
        self.root.bind('<F5>',lambda _:self.refresh())
        self.root.bind('<Control-comma>',lambda _:self._open_settings())
        self.tray = Tray(self.events,self.settings['tray_icons'])
        self.controller = Controller(providers,self.settings['refresh_seconds'],self.settings['debug_logging'])
        self.controller.start()
        self.last_tick = time.monotonic()
        self.device_refresh_at = None
        self.tick_id = self.root.after(100,self._tick)
        if hidden:self.root.withdraw()
        if self.config_error:self.message.configure(text=self.config_error,fg=AMBER)

    def _style(self):
        style_controls(self.root)

    def _build(self):
        outer=tk.Frame(self.root,bg=BG,padx=32,pady=24)
        outer.pack(fill='both',expand=True)
        heading=tk.Frame(outer,bg=BG)
        heading.pack(fill='x')
        titles=tk.Frame(heading,bg=BG)
        titles.pack(side='left')
        tk.Label(titles,text='设备电量',bg=BG,fg=TEXT,font=('Microsoft YaHei UI',25,'bold')).pack(anchor='w')
        tk.Label(titles,text='电量、连接与个性设置，一处掌握。',bg=BG,fg=MUTED,font=('Microsoft YaHei UI',10)).pack(anchor='w',pady=(4,0))
        self.refresh_button=PillButton(heading,'↻  刷新电量',self.refresh,primary=True,width=120)
        self.refresh_button.pack(side='right',padx=(10,0))
        PillButton(heading,'偏好设置',self._open_settings,width=100).pack(side='right')
        section=tk.Frame(outer,bg=BG)
        section.pack(fill='x',pady=(20,12))
        tk.Label(section,text='我的设备',bg=BG,fg=TEXT,font=('Microsoft YaHei UI',10,'bold')).pack(side='left')
        self.overview=tk.Label(section,text='正在连接设备…',bg=BG,fg=MUTED,font=('Microsoft YaHei UI',9))
        self.overview.pack(side='right')
        card_area=tk.Frame(outer,bg=BG)
        card_area.pack(fill='both',expand=True)
        self.card_canvas=tk.Canvas(card_area,bg=BG,highlightthickness=0,height=1)
        self.card_scrollbar=ttk.Scrollbar(card_area,orient='vertical',command=self.card_canvas.yview)
        self.card_canvas.pack(side='left',fill='both',expand=True)
        self.card_canvas.configure(yscrollcommand=self.card_scrollbar.set)
        card_content=tk.Frame(self.card_canvas,bg=BG)
        content_id=self.card_canvas.create_window((0,0),window=card_content,anchor='nw')
        def sync_cards(event=None):
            self.card_canvas.itemconfigure(content_id,width=self.card_canvas.winfo_width())
            bounds=self.card_canvas.bbox('all')
            self.card_canvas.configure(scrollregion=bounds)
            if bounds and bounds[3]>self.card_canvas.winfo_height()+1:
                if not self.card_scrollbar.winfo_manager():self.card_scrollbar.pack(side='right',fill='y',padx=(8,0))
            else:
                self.card_scrollbar.pack_forget()
                self.card_canvas.yview_moveto(0)
        card_content.bind('<Configure>',sync_cards)
        self.card_canvas.bind('<Configure>',sync_cards)
        def scroll(event):
            bounds=self.card_canvas.bbox('all')
            if bounds and bounds[3]>self.card_canvas.winfo_height():
                self.card_canvas.yview_scroll(-int(event.delta/120),'units')
        def bind_scroll(widget):
            widget.bind('<MouseWheel>',scroll)
            for child in widget.winfo_children():bind_scroll(child)
        settings_actions={'logitech_gpw':self._open_gpw_settings,'vgn_s99':self._open_s99_settings,'leobog_alice75':self._open_alice75_settings}
        self.cards={s.device_id:DeviceCard(card_content,s.device_name,'mouse' if s.device_id=='logitech_gpw' else 'keyboard',settings_actions.get(s.device_id)) for s in self.provider_states}
        bind_scroll(card_area)
        bottom=tk.Frame(outer,bg=BG)
        bottom.pack(fill='x',pady=(14,0))
        self.message=tk.Label(bottom,text=f"每 {self.settings['refresh_seconds']} 秒自动刷新 · 本机读取",bg=BG,fg=MUTED,
                              anchor='w',justify='left',wraplength=510,font=('Microsoft YaHei UI',9))
        self.message.pack(side='left',fill='x',expand=True)
        PillButton(bottom,'收起到托盘',self.root.withdraw,width=116).pack(side='right')

    def _update_overview(self):
        online=sum(s.connected is True for s in self.states.values())
        offline=sum(s.connection_status=='offline' for s in self.states.values())
        reading=sum(s.connection_status=='reading' for s in self.states.values())
        unknown=len(self.provider_states)-online-offline-reading
        parts=[f'{len(self.provider_states)} 台设备',f'{online} 台在线']
        if offline:parts.append(f'{offline} 台离线')
        if reading:parts.append(f'{reading} 台读取中')
        if unknown:parts.append(f'{unknown} 台状态待确认')
        self.overview.configure(text='  ·  '.join(parts))

    def _open_s99_settings(self):
        if self.s99_dialog and not self.s99_dialog.closed:
            self.s99_dialog.window.deiconify()
            self.s99_dialog.window.lift()
            return self.s99_dialog
        from ui.s99_settings import S99SettingsWindow
        self.s99_dialog=S99SettingsWindow(self.root)
        return self.s99_dialog

    def _open_gpw_settings(self):
        if self.gpw_dialog and not self.gpw_dialog.closed:
            self.gpw_dialog.window.deiconify()
            self.gpw_dialog.window.lift()
            return self.gpw_dialog
        from ui.gpw_settings import GpwSettingsWindow
        self.gpw_dialog=GpwSettingsWindow(self.root)
        return self.gpw_dialog

    def _close_settings(self):
        if self.settings_dialog is not None:
            self.settings_dialog.destroy()
            self.settings_dialog=None

    def _open_alice75_settings(self):
        if self.alice75_dialog and not self.alice75_dialog.closed:
            self.alice75_dialog.window.deiconify()
            self.alice75_dialog.window.lift()
            return self.alice75_dialog
        from ui.leobog_settings import Alice75SettingsWindow
        self.alice75_dialog=Alice75SettingsWindow(self.root)
        return self.alice75_dialog

    def _close_device_settings(self):
        dialogs=[d for d in (self.gpw_dialog,self.s99_dialog,self.alice75_dialog) if d and not d.closed]
        if any(d.busy for d in dialogs):
            for d in dialogs:
                if d.busy:d.set_status('正在与设备通信，完成后即可关闭。')
            return False
        for d in dialogs:d.close()
        return True

    def _open_settings(self):
        if self.settings_dialog is not None and self.settings_dialog.winfo_exists():
            self.settings_dialog.lift()
            return self.settings_dialog
        dialog=tk.Toplevel(self.root)
        self.settings_dialog=dialog
        dialog.title('偏好设置')
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.resizable(False,False)
        dialog.protocol('WM_DELETE_WINDOW',self._close_settings)
        dialog.bind('<Escape>',lambda _:self._close_settings())
        outer=tk.Frame(dialog,bg=BG,padx=30,pady=26)
        outer.pack(fill='both',expand=True)
        tk.Label(outer,text='偏好设置',bg=BG,fg=TEXT,font=('Microsoft YaHei UI',20,'bold')).pack(anchor='w')
        tk.Label(outer,text='按你的习惯运行。',bg=BG,fg=MUTED).pack(anchor='w',pady=(5,22))
        self.interval=tk.StringVar(value=str(self.settings['refresh_seconds']))
        self.threshold=tk.StringVar(value=str(self.settings['low_threshold']))
        self.mode=tk.StringVar(value={'combined':'合并图标','both':'分别显示','mouse':'仅鼠标','keyboard':'仅键盘'}[self.settings['tray_icons']])
        group=RoundedPanel(outer,padding=18)
        group.pack(fill='x')
        fields=group.content
        for row,title in enumerate(('刷新间隔（秒）','低电量提醒（%）','托盘图标')):
            tk.Label(fields,text=title,bg=SURFACE,fg=TEXT,anchor='w',width=24).grid(row=row,column=0,sticky='w',pady=8)
        ttk.Spinbox(fields,from_=30,to=1800,textvariable=self.interval,width=16).grid(row=0,column=1)
        ttk.Spinbox(fields,from_=1,to=99,textvariable=self.threshold,width=16).grid(row=1,column=1)
        ttk.Combobox(fields,textvariable=self.mode,values=['合并图标','分别显示','仅鼠标','仅键盘'],state='readonly',width=16).grid(row=2,column=1)
        tk.Frame(outer,bg=BORDER,height=1).pack(fill='x',pady=(18,10))
        self.startup=tk.BooleanVar(value=autostart.enabled())
        self.close_to_tray=tk.BooleanVar(value=self.settings['close_to_tray'])
        self.debug=tk.BooleanVar(value=self.settings['debug_logging'])
        for text,var in [('开机时启动',self.startup),('关闭窗口后继续在托盘运行',self.close_to_tray),('记录调试日志',self.debug)]:
            Switch(outer,text,var).pack(fill='x')
        tk.Label(outer,text='配置与日志保存在 '+str(settings.RUNTIME),bg=BG,fg=MUTED,
                 font=('Microsoft YaHei UI',8)).pack(anchor='w',pady=(16,0))
        buttons=tk.Frame(outer,bg=BG)
        buttons.pack(fill='x',pady=(24,0))
        tk.Button(buttons,text='完全退出',command=self.quit,bg=BG,fg=MUTED,
                  activebackground=BG,activeforeground=RED,bd=0,highlightthickness=0,
                  cursor='hand2',padx=0).pack(side='left')
        PillButton(buttons,'保存设置',self.save_settings,primary=True).pack(side='right',padx=(10,0))
        PillButton(buttons,'取消',self._close_settings,width=76).pack(side='right')
        dialog.update_idletasks()
        group.configure(width=fields.winfo_reqwidth()+2*group.padding)
        dialog.update_idletasks()
        x=self.root.winfo_rootx()+(self.root.winfo_width()-dialog.winfo_reqwidth())//2
        y=self.root.winfo_rooty()+max(0,(self.root.winfo_height()-dialog.winfo_reqheight())//2)
        dialog.geometry(f'+{max(0,x)}+{max(0,y)}')
        dialog.grab_set()
        return dialog

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def refresh(self):
        if hasattr(self,'controller'):
            # Keep the last reading visible while querying, including after resume.
            for key,state in list(self.states.items()): self.mark_reading(key)
            self.controller.refresh()
        self.message.configure(text='正在查询；休眠或暂时无响应时显示上次电量与读取时间。',fg=MUTED)

    def mark_reading(self,key):
        previous = self.states.get(key)
        if previous:
            state = BatteryState(key,previous.device_name,last_updated=previous.last_updated,connection_status='reading',
                                 receiver_connected=previous.receiver_connected,
                                 last_reading=previous.current_reading() or previous.last_reading)
            self.states[key] = state
            self.cards[key].render(state)
            self.tray.update(self.states)
            self._update_overview()

    def _apply_state(self,state):
        self.states[state.device_id] = state
        self.cards[state.device_id].render(state)
        self.tray.update(self.states)
        self._update_overview()
        if self.gate.should_notify(state,self.settings['low_threshold']):
            self.tray.notify('设备电量偏低',f'{state.device_name}：{state.display_battery}'+('（电压估算）' if state.estimated else '')+('；充电状态未知' if state.charging is None else ''))

    def _tick(self):
        if self.quitting: return
        try:
            current = time.monotonic()
            if current-self.last_tick>8: self.refresh()
            self.last_tick = current
            self.tray.pump()
            while True:
                try: kind,payload = self.controller.events.get_nowait()
                except queue.Empty: break
                if kind=='state': self._apply_state(payload)
                elif kind=='reading': self.mark_reading(payload)
                elif kind=='warning': self.message.configure(text=payload,fg=AMBER)
            while True:
                try: kind,payload = self.events.get_nowait()
                except queue.Empty: break
                if kind=='quit':
                    self.quit()
                    if self.quitting:return
                if kind=='open': self.show()
                if kind in ('resume','refresh'): self.refresh()
                if kind=='devices': self.device_refresh_at=current+0.5
                if kind=='tray_rebuild': self.tray.update(self.states,rebuild=True)
            if self.device_refresh_at is not None and current>=self.device_refresh_at:
                self.device_refresh_at = None
                self.refresh()
        except Exception:
            self.message.configure(text='界面或托盘更新失败；请完全退出后重试。',fg=RED)
        self.tick_id = self.root.after(150,self._tick)

    def save_settings(self):
        try:
            new = settings.validate(dict(refresh_seconds=int(self.interval.get()),low_threshold=int(self.threshold.get()),tray_icons={'合并图标':'combined','分别显示':'both','仅鼠标':'mouse','仅键盘':'keyboard'}[self.mode.get()],close_to_tray=self.close_to_tray.get(),debug_logging=self.debug.get()))
            if self.startup.get()!=autostart.enabled(): autostart.set_enabled(self.startup.get())
            settings.save(new)
            self.settings = new
            self.controller.configure(new['refresh_seconds'],new['debug_logging'])
            self.tray.update(self.states,new['tray_icons'])
            self.message.configure(text=f"设置已保存 · 每 {new['refresh_seconds']} 秒自动刷新",fg=MUTED)
            self._close_settings()
        except (ValueError,OSError,KeyError) as exc: messagebox.showerror('设置未保存',str(exc),parent=self.root)

    def on_close(self):
        if not self._close_device_settings():return
        self._close_settings()
        if self.settings['close_to_tray']: self.root.withdraw()
        else: self.quit()

    def quit(self):
        if self.quitting: return
        if not self._close_device_settings():return
        self.quitting = True
        self._close_settings()
        self.root.after_cancel(self.tick_id)
        self.root.withdraw()
        self.controller.stop()
        self.tray.close()
        self.root.destroy()
