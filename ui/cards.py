"""Compact battery cards; technical fields are available in a details window."""
import tkinter as tk
from ui.widgets import (BG,SURFACE,TEXT,MUTED,GREEN,AMBER,RED,ACCENT,
                        RoundedPanel,PillButton,battery_color,device_image,rounded)

STATUS = {'online':'在线','offline':'接收器离线','unsupported':'暂不支持',
          'unresponsive':'未响应','unknown':'状态未知','reading':'读取中'}
CHARGE = {'discharging':'放电中','not_charging':'未在充电','charging':'充电中',
          'charging_slow':'缓慢充电','charging_final':'充电即将完成',
          'full':'已充满','error':'充电异常','unknown':'充电状态未知'}


class DeviceCard:
    def __init__(self,parent,name,kind,on_settings=None):
        self.name,self.kind,self.state = name,kind,None
        self.frame = RoundedPanel(parent,padding=16)
        self.frame.pack(fill='x',pady=(0,8))
        content=self.frame.content
        body=tk.Frame(content,bg=SURFACE)
        body.pack(fill='x')
        self.art=device_image(body,kind,name)
        self.artwork=tk.Label(body,image=self.art,bg=SURFACE,bd=0)
        self.artwork.pack(side='left',padx=(0,22))
        battery=tk.Frame(body,bg=SURFACE,width=128)
        battery.pack(side='right',padx=(16,4))
        self.value=tk.Label(battery,text='—',bg=SURFACE,fg=MUTED,font=('Segoe UI',30),anchor='e',width=7)
        self.value.pack(anchor='e')
        self.bar=tk.Canvas(battery,width=112,height=5,bg=SURFACE,highlightthickness=0)
        self.bar.pack(anchor='e',pady=(4,3))
        self.percent=None
        self.bar_level=None
        self.animation=None
        self.bar.bind('<Configure>',lambda _:self.draw_bar())
        self.bar.bind('<Destroy>',self._cleanup,add='+')
        info=tk.Frame(body,bg=SURFACE)
        info.pack(side='left',fill='x',expand=True)
        tk.Label(info,text=name,bg=SURFACE,fg=TEXT,font=('Microsoft YaHei UI',12,'bold'),anchor='w').pack(fill='x')
        self.status=tk.Label(info,text='●  等待连接',bg=SURFACE,fg=MUTED,
                             font=('Microsoft YaHei UI',9),anchor='w')
        self.status.pack(anchor='w',pady=(5,0))
        self.detail=tk.Label(info,text='正在获取设备状态',bg=SURFACE,fg=MUTED,
                             font=('Microsoft YaHei UI',9),anchor='w')
        self.detail.pack(fill='x',pady=(5,0))
        footer=tk.Frame(content,bg=SURFACE)
        footer.pack(fill='x',pady=(9,0))
        self.note=tk.Label(footer,text='等待首次读取',bg=SURFACE,fg=MUTED,
                           font=('Microsoft YaHei UI',8),anchor='w')
        self.note.pack(side='left',fill='x',expand=True)
        PillButton(footer,'详情',self.show_details,width=62,height=28,bg=SURFACE).pack(side='right')
        if on_settings:
            PillButton(footer,'设备设置  ›',on_settings,width=100,height=28,bg=SURFACE).pack(side='right',padx=(0,8))

    def draw_bar(self):
        self.bar.delete('all')
        width=self.bar.winfo_width()
        rounded(self.bar,0,0,width,5,2.5,fill='#e9e9ed',outline='')
        if self.bar_level is not None:
            if self.bar_level>0:
                rounded(self.bar,0,0,max(2,width*self.bar_level/100),5,2.5,
                        fill=MUTED if self.state and self.state.uses_last_reading else battery_color(self.percent),outline='')

    def _cleanup(self,event):
        if event.widget==self.bar and self.animation is not None:
            self.bar.after_cancel(self.animation)
            self.animation=None

    def animate_bar(self):
        if self.animation is not None:
            self.bar.after_cancel(self.animation)
            self.animation=None
        if self.percent is None:
            # Never animate stale data out when a receiver disappears.
            self.bar_level=None
            self.draw_bar()
            return
        start=self.bar_level or 0
        target=self.percent
        def step(frame=1):
            self.animation=None
            t=frame/10
            self.bar_level=start+(target-start)*(1-(1-t)**3)
            self.draw_bar()
            if frame<10:self.animation=self.bar.after(18,lambda:step(frame+1))
        step()

    def render(self,state):
        self.state,self.percent=state,state.display_percent
        # Keep healthy percentages quiet; reserve color for the progress and status.
        fg=MUTED if state.uses_last_reading else (TEXT if self.percent is not None and self.percent>=30 else battery_color(self.percent))
        self.value.configure(text=state.display_battery,fg=fg)
        color=GREEN if state.connected is True else MUTED
        if state.connection_status=='unresponsive':color=AMBER
        if state.connection_status=='reading':color=ACCENT
        status=STATUS.get(state.connection_status,'状态未知')
        if state.uses_last_reading:
            status=('休眠/未响应' if state.connection_status=='unresponsive' else status)+' · 上次读数'
        self.status.configure(text='●  '+status,fg=color)
        detail=('无线鼠标' if self.kind=='mouse' else '无线键盘')+'  ·  '+CHARGE.get(state.charge_status,'充电状态未知')
        if state.estimated:detail+='  ·  电压估算'
        if state.connection_status=='offline':detail='连接接收器后，将自动读取电量'
        elif state.connection_status=='unsupported':detail='电量暂不可用，可查看详情'
        elif state.connection_status=='unresponsive':detail='设备可能正在休眠，唤醒后重试'
        elif state.connection_status=='reading':detail='正在获取最新电量…'
        if state.uses_last_reading:
            detail={'reading':'正在刷新，暂显示上次电量',
                    'offline':'接收器已断开，显示上次电量',
                    'unsupported':'本次读取不可用，显示上次电量'}.get(state.connection_status,'显示上次电量，唤醒后自动更新')
        self.detail.configure(text=detail)
        stamp=state.display_updated.replace('T',' ')[:19] if state.display_updated else None
        label='上次读取' if state.uses_last_reading else ('最近更新' if state.battery_percent is not None else '上次成功读取')
        self.note.configure(text=f'{label}  {stamp}' if stamp else '尚无成功读数')
        self.animate_bar()

    def show_details(self):
        dialog=tk.Toplevel(self.frame)
        dialog.title(self.name+' · 详情')
        dialog.configure(bg=BG)
        dialog.transient(self.frame.winfo_toplevel())
        dialog.resizable(False,False)
        content=tk.Frame(dialog,bg=BG,padx=28,pady=24)
        content.pack(fill='both',expand=True)
        tk.Label(content,text=self.name,bg=BG,fg=TEXT,font=('Microsoft YaHei UI',15,'bold')).pack(anchor='w',pady=(0,16))
        state=self.state
        historical=state.last_reading if state and state.uses_last_reading else None
        source=historical.data_source if historical else (state.data_source if state else '')
        voltage=historical.voltage_mv if historical else (state.voltage_mv if state else None)
        rows=[('电量',state.display_battery if state else '未知'),
              ('读数状态','上次读数（非实时）' if historical else '本次读取'),
              ('连接状态',STATUS.get(state.connection_status,'状态未知') if state else '等待首次读取'),
              ('充电状态',CHARGE.get(state.charge_status,'充电状态未知') if state else '未知'),
              ('数据来源',source or '—'),
              ('电压（上次）' if historical else '电压',f'{voltage} mV' if voltage is not None else '—'),
              ('最近成功读取',state.display_updated.replace('T',' ') if state and state.display_updated else '—')]
        for title,value in rows:
            row=tk.Frame(content,bg=BG)
            row.pack(fill='x',pady=5)
            tk.Label(row,text=title,width=13,anchor='w',bg=BG,fg=MUTED,font=('Microsoft YaHei UI',10)).pack(side='left')
            tk.Label(row,text=value,anchor='w',bg=BG,fg=TEXT,font=('Microsoft YaHei UI',10),wraplength=340,justify='left').pack(side='left')
        if state and state.display_estimated:
            tk.Label(content,text='电量由电压估算，尚未针对这台 GPW 单独标定。',bg=BG,fg=AMBER,
                     font=('Microsoft YaHei UI',9),wraplength=430,justify='left').pack(anchor='w',pady=(14,0))
        if state and state.error_message:
            tk.Label(content,text=state.error_message,bg=BG,fg=MUTED,font=('Microsoft YaHei UI',9),
                     wraplength=430,justify='left').pack(anchor='w',pady=(14,0))
        PillButton(content,'关闭',dialog.destroy,width=84,primary=True).pack(anchor='e',pady=(20,0))
        dialog.bind('<Escape>',lambda _:dialog.destroy())
        return dialog
