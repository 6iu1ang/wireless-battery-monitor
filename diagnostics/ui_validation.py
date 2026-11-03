"""One UI review cycle; screenshots only this program's own windows."""
import json
import tkinter as tk
from config.settings import ROOT,prepare_environment,load
from diagnostics.gui_validation import pump,screenshot
from providers.logitech_hidpp import LogitechProvider
from providers.vgn_s99 import VgnS99Provider
from providers.leobog_alice75 import LeobogAlice75Provider
from ui.dashboard import Dashboard


def run():
    prepare_environment()
    saved,_=load()
    root=tk.Tk()
    app=Dashboard(root,[LogitechProvider(),VgnS99Provider(),LeobogAlice75Provider()])
    result={}
    try:
        pump(root,2)
        assert len(app.states)==3 and len(app.cards)==3
        for card in app.cards.values():
            assert card.frame.winfo_height()>100,'device cards must have visible content'
            assert card.value.winfo_ismapped(),'battery labels must be mapped'
        assert set(app.tray.icons)=={3},'preserve the single combined icon'
        bounds=app.card_canvas.bbox('all')
        result['default_geometry']=root.geometry()
        result['all_cards_visible']=bounds[3]<=app.card_canvas.winfo_height()
        assert result['all_cards_visible'],'all three cards should fit at the default size'
        screenshot(root,ROOT/'diagnostics'/'dashboard-openai.png')
        dialog=app._open_settings()
        pump(root,0.2)
        screenshot(dialog,ROOT/'diagnostics'/'settings-openai.png')
        app.interval.set('120')
        app._close_settings()
        app._open_settings()
        assert app.interval.get()==str(saved['refresh_seconds']),'cancel must discard changes'
        app._close_settings()
        result['settings_open_cancel_reopen']=True
        details=app.cards['leobog_alice75'].show_details()
        pump(root,0.1)
        assert details.winfo_exists()
        details.destroy()
        result['device_details']=True
        root.geometry('800x560');pump(root,0.2)
        app.card_canvas.yview_moveto(1);pump(root,0.1)
        assert app.card_canvas.yview()[1]>=0.99
        result['small_window_scroll']=True
        app.refresh()
        assert all(s.battery_percent is None for s in app.states.values())
        pump(root,2)
        assert len(app.states)==3
        result['states']={k:s.to_dict() for k,s in app.states.items()}
        app.on_close();root.update()
        assert root.state()=='withdrawn' and len(app.tray.icons)==1
        result['close_to_tray']=True
    finally:
        app.quit()
    assert not app.tray.icons and not any(t.is_alive() for t in app.controller.threads)
    assert load()[0]==saved,'saved preferences must remain unchanged'
    result.update(cleanup_ok=True,preferences_preserved=True)
    (ROOT/'diagnostics'/'ui_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':run()
