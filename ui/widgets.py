"""Shared visual system. Runtime rendering uses Tk and bundled PNGs only."""
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from math import cos, sin, radians

BG = '#f5f5f7'
SURFACE = '#ffffff'
TEXT = '#1d1d1f'
MUTED = '#6e6e73'
BORDER = '#e4e4e9'
ACCENT = '#0071e3'
TINT = '#edf5ff'
GREEN = '#24834b'
AMBER = '#986500'
RED = '#c13434'


def rounded(canvas, x1, y1, x2, y2, radius=18, **options):
    r = max(0,min(radius, (x2-x1)/2, (y2-y1)/2))
    points=[]
    for cx,cy,start in ((x2-r,y1+r,-90),(x2-r,y2-r,0),
                        (x1+r,y2-r,90),(x1+r,y1+r,180)):
        for step in range(7):
            angle=radians(start+step*15)
            points.extend((cx+r*cos(angle),cy+r*sin(angle)))
    return canvas.create_polygon(*points,smooth=True,splinesteps=12,**options)


class RoundedPanel(tk.Canvas):
    def __init__(self, parent, padding=20, **kwargs):
        super().__init__(parent,bg=BG,highlightthickness=0,bd=0,height=1,**kwargs)
        self.padding = padding
        self.content = tk.Frame(self,bg=SURFACE)
        self.window = self.create_window(padding,padding,window=self.content,anchor='nw')
        self.bind('<Configure>',self._resize)
        self.content.bind('<Configure>',self._fit_height)
        # A one-pixel canvas can clip the embedded frame before Tk delivers its
        # first Configure event; explicitly fit after child geometry is ready.
        self.after_idle(self._fit_height)

    def _fit_height(self,event=None):
        height = self.content.winfo_reqheight()+2*self.padding
        if int(float(self.cget('height'))) != height:self.configure(height=height)

    def _resize(self,event):
        self.itemconfigure(self.window,width=max(1,event.width-2*self.padding))
        self.delete('surface')
        rounded(self,2,4,event.width-2,event.height-1,24,
                fill='#eaeaef',outline='',tags='surface')
        rounded(self,1,1,event.width-1,event.height-3,24,
                fill=SURFACE,outline='#eeeeF2',width=1,tags='surface')
        self.tag_lower('surface')


class PillButton(tk.Canvas):
    def __init__(self,parent,text,command,width=104,height=38,primary=False,bg=BG):
        super().__init__(parent,width=width,height=height,bg=bg,bd=0,
                         highlightthickness=0,cursor='hand2',takefocus=True)
        self.label,self.command,self.primary = text,command,primary
        self.hover = self.pressed = False
        self.bind('<Configure>',lambda _:self.draw())
        self.bind('<Enter>',lambda _:self._hover(True))
        self.bind('<Leave>',lambda _:self._hover(False))
        self.bind('<FocusIn>',lambda _:self.draw())
        self.bind('<FocusOut>',lambda _:self.draw())
        self.bind('<Button-1>',self._press)
        self.bind('<ButtonRelease-1>',self._click)
        self.bind('<Return>',self._keyboard)
        self.bind('<space>',self._keyboard)
        self.bind('<Escape>',self._cancel)
        self.draw()

    def _hover(self,value):
        self.hover=value
        self.draw()

    def _press(self,event):
        if self.cget('state')=='disabled':return
        self.focus_set()
        self.pressed=True
        self.draw()

    def _keyboard(self,event):
        if self.cget('state')!='disabled':self.command()
        return 'break'

    def _cancel(self,event):
        self.pressed=False
        self.draw()

    def _click(self,event):
        activate=self.cget('state')!='disabled' and self.pressed and 0<=event.x<self.winfo_width() and 0<=event.y<self.winfo_height()
        self.pressed=False
        self.draw()
        if activate:self.command()

    def configure(self,cnf=None,**kwargs):
        result=super().configure(cnf,**kwargs)
        if 'state' in kwargs and hasattr(self,'label'):self.draw()
        return result

    config=configure

    def draw(self):
        self.delete('all')
        w=max(self.winfo_width(),int(self.cget('width')))
        h=max(self.winfo_height(),int(self.cget('height')))
        active=self.pressed and self.hover
        disabled=self.cget('state')=='disabled'
        fill=('#005bb8' if active else '#0077ed' if self.hover else ACCENT) if self.primary else ('#e5e5eb' if active else '#f0f0f5' if self.hover else SURFACE)
        if self.focus_get()==self:
            rounded(self,0,0,w,h,13,fill='#c0dcff',outline='')
        if disabled:fill='#f0f0f3'
        rounded(self,2,2,w-2,h-2,11,fill=fill,outline=fill if self.primary else BORDER,width=1)
        self.create_text(w/2,h/2+(1 if active else 0),text=self.label,fill='#929297' if disabled else SURFACE if self.primary else TEXT,
                         font=('Microsoft YaHei UI',9 if h<34 else 10))


def device_image(master,kind,name='',compact=False):
    """Photo assets exported at native UI sizes; no runtime image dependency."""
    filename='mouse' if kind=='mouse' else 'alice75' if 'alice' in name.lower() else 's99'
    variant='compact' if compact else 'card'
    return tk.PhotoImage(master=master,file=str(Path(__file__).parent/'assets'/f'{filename}-{variant}.png'))


def style_controls(master):
    style=ttk.Style(master)
    style.theme_use('clam')
    for kind in ('TCombobox','TSpinbox','TEntry'):
        style.configure(kind,fieldbackground=SURFACE,background=SURFACE,foreground=TEXT,
                        bordercolor=BORDER,lightcolor=BORDER,darkcolor=BORDER,
                        arrowcolor=MUTED,padding=6,insertcolor=TEXT)
        style.map(kind,bordercolor=[('focus',ACCENT)],lightcolor=[('focus',ACCENT)],
                  darkcolor=[('focus',ACCENT)],foreground=[('disabled','#929297')],
                  fieldbackground=[('disabled','#f5f5f7'),('readonly',SURFACE)],
                  selectbackground=[('readonly',SURFACE)],selectforeground=[('readonly',TEXT)])
    style.configure('Vertical.TScrollbar',background='#d1d1d6',troughcolor=BG,
                    borderwidth=0,arrowsize=10,lightcolor=BG,darkcolor=BG)
    style.map('Vertical.TScrollbar',background=[('active','#aeaeb5')])
    style.configure('TCheckbutton',background=SURFACE,foreground=TEXT,padding=3,
                    indicatorbackground=SURFACE,indicatorforeground=ACCENT)
    style.map('TCheckbutton',background=[('active',SURFACE)],
              indicatorbackground=[('selected',ACCENT)],indicatorforeground=[('selected',SURFACE)])
    style.configure('S99.TNotebook',background=SURFACE,borderwidth=0,tabmargins=0,
                    bordercolor=SURFACE,lightcolor=SURFACE,darkcolor=SURFACE)
    style.layout('S99.TNotebook',[('Notebook.client',{'sticky':'nswe'})])
    style.layout('S99.TNotebook.Tab',[])
    style.configure('S99.TButton',padding=(11,6),background=SURFACE,foreground=ACCENT,
                    bordercolor=BORDER,lightcolor=BORDER,darkcolor=BORDER,
                    font=('Microsoft YaHei UI',10),focusthickness=1,focuscolor=ACCENT)
    style.map('S99.TButton',background=[('pressed','#dcecff'),('active',TINT)],
              foreground=[('disabled','#929297')],bordercolor=[('focus',ACCENT)])
    style.configure('S99.Treeview',rowheight=30,background=SURFACE,fieldbackground=SURFACE,
                    foreground=TEXT,font=('Microsoft YaHei UI',10),borderwidth=0)
    style.configure('S99.Treeview.Heading',background='#f5f5f7',foreground=MUTED,
                    padding=7,font=('Microsoft YaHei UI',9),relief='flat')
    style.map('S99.Treeview',background=[('selected',TINT)],foreground=[('selected','#0056ac')])
    return style


class SegmentedNotebook(ttk.Notebook):
    """Native Notebook pages with a keyboard-accessible segmented selector."""
    def __init__(self,parent,**kwargs):
        super().__init__(parent,style='S99.TNotebook',**kwargs)
        self.nav=tk.Canvas(parent,height=42,bg=BG,bd=0,highlightthickness=0,takefocus=True,cursor='hand2')
        self.nav.bind('<Configure>',lambda _:self.draw_nav())
        self.nav.bind('<Button-1>',self._choose)
        self.nav.bind('<Left>',lambda e:self._step(-1))
        self.nav.bind('<Right>',lambda e:self._step(1))
        self.nav.bind('<Home>',lambda e:self._goto(0))
        self.nav.bind('<End>',lambda e:self._goto(len(self.tabs())-1))
        self.nav.bind('<FocusIn>',lambda _:self.draw_nav())
        self.nav.bind('<FocusOut>',lambda _:self.draw_nav())
        self.bind('<<NotebookTabChanged>>',lambda _:self.draw_nav())

    def pack(self,**kwargs):
        self.nav.pack(fill='x',padx=kwargs.get('padx',0),pady=(0,12))
        super().pack(**kwargs)

    def add(self,child,**kwargs):
        super().add(child,**kwargs)
        self.draw_nav()

    def tab(self,tab_id,option=None,**kwargs):
        result=super().tab(tab_id,option,**kwargs)
        if kwargs:self.draw_nav()
        return result

    def _goto(self,index):
        if self.tabs():self.select(self.tabs()[index])
        return 'break'

    def _step(self,delta):
        return self._goto((self.index(self.select())+delta)%len(self.tabs()))

    def _choose(self,event):
        self.nav.focus_set()
        if self.tabs():self._goto(min(len(self.tabs())-1,max(0,int(event.x/max(1,self.nav.winfo_width())*len(self.tabs())))))

    def draw_nav(self):
        self.nav.delete('all')
        w,h=self.nav.winfo_width(),42
        rounded(self.nav,0,0,w,h,12,fill='#e8e8ed',outline='')
        tabs=self.tabs()
        if not tabs:return
        width=w/len(tabs)
        selected=self.select()
        for i,tab in enumerate(tabs):
            if tab==selected:
                rounded(self.nav,i*width+3,4,(i+1)*width-3,h-2,10,fill='#d8d8df',outline='')
                rounded(self.nav,i*width+3,3,(i+1)*width-3,h-4,10,fill=SURFACE,
                        outline=ACCENT if self.nav.focus_get()==self.nav else '#dedee5')
            self.nav.create_text((i+.5)*width,h/2-1,text=super().tab(tab,'text'),
                                 fill=TEXT if tab==selected else MUTED,font=('Microsoft YaHei UI',10))


class Switch(tk.Canvas):
    """A labeled switch with mouse, Space and Return support."""
    def __init__(self,parent,text,variable,bg=BG):
        super().__init__(parent,height=40,bg=bg,bd=0,highlightthickness=0,
                         takefocus=True,cursor='hand2')
        self.label,self.variable=text,variable
        self.trace=variable.trace_add('write',lambda *_:self.draw())
        self.bind('<Configure>',lambda _:self.draw())
        self.bind('<Button-1>',self.toggle)
        self.bind('<space>',self.toggle)
        self.bind('<Return>',self.toggle)
        self.bind('<FocusIn>',lambda _:self.draw())
        self.bind('<FocusOut>',lambda _:self.draw())
        self.bind('<Destroy>',self._cleanup)

    def _cleanup(self,event):
        if event.widget==self:self.variable.trace_remove('write',self.trace)

    def toggle(self,event=None):
        self.focus_set()
        self.variable.set(not self.variable.get())
        return 'break'

    def draw(self):
        self.delete('all')
        w=self.winfo_width()
        self.create_text(0,20,text=self.label,fill=TEXT,font=('Microsoft YaHei UI',10),anchor='w')
        if self.focus_get()==self:
            rounded(self,w-50,4,w,36,16,fill='#c0dcff',outline='')
        rounded(self,w-48,7,w-2,33,13,fill=ACCENT if self.variable.get() else '#d1d1d6',outline='')
        x=w-15 if self.variable.get() else w-35
        self.create_oval(x-10,10,x+10,31,fill='#bfc0c6',outline='')
        self.create_oval(x-10,9,x+10,29,fill=SURFACE,outline='')


def battery_color(percent):
    if percent is None:return '#9b9b9b'
    return GREEN if percent>=30 else (AMBER if percent>=15 else RED)
