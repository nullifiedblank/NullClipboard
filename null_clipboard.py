# null_clipboard_polished.py
import os
import sys
import json
import time
import io
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from PIL import Image, ImageTk, ImageGrab, ImageDraw, ImageFilter
import pyperclip
import win32clipboard
import keyboard
import winreg
from pystray import Icon as TrayIcon, MenuItem as TrayItem

# ---------------------------
# Modern UI Constants
# ---------------------------
BG_PRIMARY = "#1e1e1e"
BG_SECONDARY = "#2d2d2d"
TEXT_PRIMARY = "#e0e0e0"
ACCENT = "#007acc"
SUCCESS = "#4caf50"
FONT_FAMILY = "Segoe UI"
FONT_NORMAL = (FONT_FAMILY, 10)
FONT_BOLD = (FONT_FAMILY, 12, "bold")


# ---------------------------
# Settings persistence
# ---------------------------
SETTINGS_FILE = "settings.json"
DEFAULTS = {
    "hotkey": "ctrl+shift+0",
    "always_on_top": True,
    "run_on_startup": False,
    "autoclose": False,
    "history_limit": 50
}

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
            # ensure keys exist
            for k, v in DEFAULTS.items():
                if k not in s:
                    s[k] = v
            return s
        except Exception:
            return DEFAULTS.copy()
    return DEFAULTS.copy()

def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except Exception as e:
        print("Failed saving settings:", e)

settings = load_settings()

# ---------------------------
# Helpers
# ---------------------------
def set_clipboard_image(pil_img):
    """Place a PIL image onto Windows clipboard as DIB"""
    output = io.BytesIO()
    pil_img.convert("RGB").save(output, "BMP")
    data = output.getvalue()[14:]
    output.close()
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    finally:
        win32clipboard.CloseClipboard()

def save_history_folder(history):
    timestamp = datetime.now().strftime("%d%m%y%H%M%S")
    folder = f"NullClipboard{timestamp}"
    os.makedirs(folder, exist_ok=True)
    for idx, item in enumerate(history, start=1):
        if item["type"] == "text":
            path = os.path.join(folder, f"clip_{idx}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(item["content"])
        else:
            path = os.path.join(folder, f"clip_{idx}.png")
            try:
                item["content"].save(path)
            except Exception:
                item["content"].save(path, "PNG")
    return os.path.abspath(folder)

def register_autorun(enable: bool):
    key_name = "NullClipboard"
    reg_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        if enable:
            exe_path = f'"{sys.executable}"'
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_WRITE)
            winreg.SetValueEx(key, key_name, 0, winreg.REG_SZ, exe_path)
            winreg.CloseKey(key)
        else:
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_WRITE)
                winreg.DeleteValue(key, key_name)
                winreg.CloseKey(key)
            except FileNotFoundError:
                pass
    except Exception as e:
        messagebox.showerror("Autorun Error", f"Could not update autorun registry: {e}")

def check_autorun():
    key_name = "NullClipboard"
    reg_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, key_name)
        winreg.CloseKey(key)
        return os.path.normpath(val).strip('"') == os.path.normpath(sys.executable)
    except Exception:
        return False

# ---------------------------
# PIL iOS-style toggle (Canvas-based but uses PIL for antialias)
# ---------------------------
class IOSToggle(tk.Label):
    """
    iOS-style pill toggle rendered with PIL for anti-aliasing.
    var = tk.BooleanVar
    command = optional callable executed after toggle
    """
    def __init__(self, parent, var: tk.BooleanVar, width=64, height=36,
                 on_color="#4cd964", off_color="#6b6b6b", command=None, **kwargs):
        super().__init__(parent, bg=BG_SECONDARY, **kwargs)
        self.parent = parent
        self.var = var
        self.width = width
        self.height = height
        self.scale = 4  # supersampling for smoothness
        self.on_color = on_color
        self.off_color = off_color
        self.command = command
        self._anim = False
        self._t = 1.0 if self.var.get() else 0.0
        self._photo = None
        self._draw(self._t)
        self.bind("<Button-1>", lambda e: self.toggle())

    def _hex_to_rgb(self, hx):
        hx = hx.lstrip("#")
        return tuple(int(hx[i:i+2], 16) for i in (0, 2, 4))

    def _blend(self, a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    def _draw(self, t):
        # t: 0..1; 0 = off, 1 = on
        W, H = self.width * self.scale, self.height * self.scale
        r = H // 2
        left = r
        right = W - r
        cx = int(left + (right - left) * t)
        knob_r = int(r * 0.85)

        im = Image.new("RGBA", (W, H), (0,0,0,0))
        draw = ImageDraw.Draw(im)

        # background interpolated color (blend off->on)
        off_rgb = self._hex_to_rgb(self.off_color)
        on_rgb = self._hex_to_rgb(self.on_color)
        bg_rgb = self._blend(off_rgb, on_rgb, t)
        draw.rounded_rectangle([0,0,W,H], radius=r, fill=(bg_rgb[0],bg_rgb[1],bg_rgb[2],255))

        # inner subtle highlight when on
        if t > 0.05:
            glow = Image.new("RGBA", (W,H), (0,0,0,0))
            gdraw = ImageDraw.Draw(glow)
            # small glow around knob
            gdraw.ellipse([cx - knob_r*1.2, r - knob_r*1.2, cx + knob_r*1.2, r + knob_r*1.2],
                          fill=(on_rgb[0], on_rgb[1], on_rgb[2], int(60 * t)))
            glow = glow.filter(ImageFilter.GaussianBlur(radius=6 * (0.5 + t)))
            im = Image.alpha_composite(im, glow)

        # knob shadow (inside)
        shadow = Image.new("RGBA", (W,H), (0,0,0,0))
        sdraw = ImageDraw.Draw(shadow)
        sdraw.ellipse([cx - knob_r, r - knob_r + 2*self.scale, cx + knob_r, r + knob_r + 2*self.scale],
                      fill=(0,0,0,50))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4))
        im = Image.alpha_composite(im, shadow)

        # knob (white)
        draw.ellipse([cx - knob_r, r - knob_r, cx + knob_r, r + knob_r], fill=(255,255,255,255))
        # subtle knob rim
        draw.ellipse([cx - knob_r, r - knob_r, cx + knob_r, r + knob_r], outline=(220,220,220,30), width= int(0.5 * self.scale))

        small = im.resize((self.width, self.height), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(small)
        self.config(image=self._photo)

    def toggle(self):
        if self._anim:
            return
        target = 1.0 if not self.var.get() else 0.0
        self.var.set(not self.var.get())
        # animate from current self._t to target
        steps = 14
        start = self._t
        self._anim = True
        for i in range(steps + 1):
            t = i / steps
            ease = 3*t*t - 2*t*t*t
            val = start + (target - start) * ease
            self.after(int(i * (1000/60)), lambda v=val: self._draw_frame(v))
        self.after(int((steps+1) * (1000/60)), self._end_anim)

        # call command and save settings
        if self.command:
            try:
                self.command()
            except Exception:
                pass
        save_settings(settings)

    def _draw_frame(self, v):
        self._t = v
        self._draw(v)

    def _end_anim(self):
        self._anim = False
        self._t = 1.0 if self.var.get() else 0.0
        self._draw(self._t)

# ---------------------------
# Main App
# ---------------------------
class NullClipboardApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Null Clipboard")
        self.root.withdraw()  # we'll use Toplevel as main UI
        self.history = []
        self.history_limit = settings.get("history_limit", DEFAULTS["history_limit"])
        self.last_clipboard = None
        self.history_window = None
        self.tray_icon = None
        self.poll_thread = None
        self._setup_styles()

        # register hotkey
        self._register_hotkey(settings.get("hotkey", DEFAULTS["hotkey"]))

        # create tray icon
        self._setup_tray()

        # start clipboard polling
        self._start_polling()

    # ---------------- UI building
    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use('clam')
        s.configure("TFrame", background=BG_PRIMARY)
        s.configure("TLabel", background=BG_PRIMARY, foreground=TEXT_PRIMARY, font=FONT_NORMAL)
        s.configure("TButton", background=ACCENT, foreground="white", font=FONT_NORMAL)
        s.map("TButton", background=[('active', '#005f9e')])
        s.configure("Vertical.TScrollbar", background=BG_SECONDARY, troughcolor=BG_PRIMARY)
        s.configure("Secondary.TFrame", background=BG_SECONDARY)
        s.configure("Secondary.TLabel", background=BG_SECONDARY, foreground=TEXT_PRIMARY, font=FONT_NORMAL)
        s.configure("Hover.TFrame", background=ACCENT)
        s.configure("Success.TFrame", background=SUCCESS)


    def show_main_window(self):
        if self.history_window and self.history_window.winfo_exists():
            self.history_window.lift()
            return

        self.history_window = tk.Toplevel(self.root)
        self.history_window.title("Null Clipboard")
        self.history_window.geometry("800x400")
        self.history_window.minsize(600, 300)
        self.history_window.configure(bg=BG_PRIMARY)
        self.history_window.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self.history_window.wm_attributes("-topmost", bool(settings.get("always_on_top", True)))

        container = ttk.Frame(self.history_window, style="TFrame")
        container.pack(fill="both", expand=True, padx=10, pady=10)

        # Left: history list
        left = ttk.Frame(container, style="TFrame")
        left.pack(side="left", fill="both", expand=True, padx=(0, 5), pady=0)

        # Canvas + scroll (hidden until hover)
        self.canvas = tk.Canvas(left, bg=BG_PRIMARY, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.canvas.yview, style="Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollable_frame = ttk.Frame(self.canvas, style="TFrame")
        self.canvas.create_window((0,0), window=self.scrollable_frame, anchor="nw")
        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        left.bind("<Enter>", lambda e: self.scrollbar.pack(side="right", fill="y"))
        left.bind("<Leave>", lambda e: self.scrollbar.pack_forget())

        # Right: pinned settings
        right = ttk.Frame(container, style="Secondary.TFrame", width=300)
        right.pack(side="right", fill="y", padx=(5, 0), pady=0)
        right.pack_propagate(False)

        ttk.Label(right, text="⚙ Settings", style="Secondary.TLabel", font=FONT_BOLD).pack(anchor="w", padx=14, pady=(10,8))

        # toggles: autoclose, always on top, run on startup
        def add_toggle_line(parent, label_text, setting_key):
            fr = ttk.Frame(parent, style="Secondary.TFrame")
            fr.pack(fill="x", padx=12, pady=6)
            ttk.Label(fr, text=label_text, style="Secondary.TLabel", font=FONT_NORMAL).pack(side="left")
            var = tk.BooleanVar(value=settings.get(setting_key, DEFAULTS.get(setting_key)))
            # command callback
            def cmd():
                settings[setting_key] = var.get()
                save_settings(settings)
                if setting_key == "always_on_top":
                    if self.history_window and self.history_window.winfo_exists():
                        self.history_window.wm_attributes("-topmost", var.get())
                if setting_key == "run_on_startup":
                    register_autorun(var.get())
            toggle = IOSToggle(fr, var, width=68, height=34, on_color=ACCENT, off_color="#6b6b6b", command=cmd)
            toggle.pack(side="right")
            return var

        self.var_autoclose = add_toggle_line(right, "Close after copy", "autoclose")
        self.var_always_on_top = add_toggle_line(right, "Always on top", "always_on_top")
        self.var_run = add_toggle_line(right, "Run on Windows startup", "run_on_startup")

        # Hotkey area
        hk_fr = ttk.Frame(right, style="Secondary.TFrame")
        hk_fr.pack(fill="x", padx=12, pady=(20,6))
        ttk.Label(hk_fr, text="Hotkey:", style="Secondary.TLabel", font=FONT_NORMAL).pack(anchor="w")
        self.hotkey_display = ttk.Label(hk_fr, text=settings.get("hotkey", DEFAULTS["hotkey"]), background=BG_PRIMARY, foreground=TEXT_PRIMARY, padding=(8, 4), font=FONT_NORMAL, style="TLabel")
        self.hotkey_display.pack(fill="x", pady=4)
        ttk.Button(hk_fr, text="Change Hotkey", command=self._open_hotkey_dialog, style="TButton").pack(fill="x", pady=(0, 4))

        # Save history button
        ttk.Button(right, text="Save Clipboard History", command=self._save_history, style="TButton").pack(fill="x", padx=12, pady=10)

        # Build UI for existing history
        self._build_history_ui()

    # ---------------- history UI helpers
    def _build_history_ui(self):
        if not (self.history_window and self.history_window.winfo_exists()):
            return
        # clear existing
        for w in self.scrollable_frame.winfo_children():
            w.destroy()
        # add items with fade-in
        for item in self.history:
            self._insert_history_item_widget(item, animate=False)

    def _insert_history_item_widget(self, item, animate=True):
        # item = {"type":"text"/"image","content":..., "ts":...}
        frame = ttk.Frame(self.scrollable_frame, style="TFrame")
        frame.pack(fill="x", pady=4, padx=6)

        frame.configure(cursor="hand2")

        # hover glow -> white border
        def on_enter(e, fr=frame):
            fr.configure(style="Hover.TFrame")
        def on_leave(e, fr=frame):
            fr.configure(style="TFrame")

        frame.bind("<Enter>", on_enter)
        frame.bind("<Leave>", on_leave)

        if item["type"] == "text":
            txt = item["content"]
            preview = txt if len(txt) <= 240 else txt[:240] + "…"
            lbl = ttk.Label(frame, text=preview, style="TLabel", wraplength=680, padding=(6,6), font=FONT_NORMAL)
            lbl.pack(fill="both", expand=True)
            lbl.bind("<Button-1>", lambda e, c=txt, fr=frame: self._on_click_text(c, fr))
            frame.bind("<Button-1>", lambda e, c=txt, fr=frame: self._on_click_text(c, fr))
        else:
            # image thumb
            img = item["content"].copy()
            thumb = img.copy()
            thumb.thumbnail((220, 220))
            photo = ImageTk.PhotoImage(thumb)
            lbl = ttk.Label(frame, image=photo, style="TLabel")
            lbl.image = photo
            lbl.pack(side="left", padx=6, pady=6)
            lbl.bind("<Button-1>", lambda e, c=item["content"], fr=frame: self._on_click_image(c, fr))
            frame.bind("<Button-1>", lambda e, c=item["content"], fr=frame: self._on_click_image(c, fr))

        # Animate fade-in (pronounced): we simulate fade by changing the background from darker to normal
        if animate:
            steps = 12
            style_name = f"Fade.TFrame.{id(frame)}"
            label_style_name = f"Fade.TLabel.{id(frame)}"
            s = ttk.Style()

            def fade(i=0):
                if i <= steps:
                    t = i / steps
                    # interpolate between darker and normal
                    base = [int(BG_PRIMARY[1:3], 16), int(BG_PRIMARY[3:5], 16), int(BG_PRIMARY[5:7], 16)]
                    normal = [int(BG_SECONDARY[1:3], 16), int(BG_SECONDARY[3:5], 16), int(BG_SECONDARY[5:7], 16)]
                    r = int(base[0] + (normal[0] - base[0]) * t)
                    g = int(base[1] + (normal[1] - base[1]) * t)
                    b = int(base[2] + (normal[2] - base[2]) * t)
                    bg_color = f"#{r:02x}{g:02x}{b:02x}"

                    s.configure(style_name, background=bg_color)
                    s.configure(label_style_name, background=bg_color, foreground=TEXT_PRIMARY)

                    frame.configure(style=style_name)
                    for child in frame.winfo_children():
                        child.configure(style=label_style_name)

                    frame.after(22, lambda: fade(i + 1))
                else:
                    frame.configure(style="TFrame")
                    for child in frame.winfo_children():
                        child.configure(style="TLabel")

            fade()
        else:
            frame.configure(style="TFrame")
            for ch in frame.winfo_children():
                ch.configure(style="TLabel")

        # store widget on item for potential update (not strictly necessary)
        item["_widget"] = frame

    # ---------------- click handlers + feedback
    def _on_click_text(self, text, frame):
        # copy and pulse green flash
        try:
            pyperclip.copy(text)
            self._pulse_green(frame)
            if settings.get("autoclose", False):
                if self.history_window:
                    self.history_window.destroy()
                    self.history_window = None
        except Exception as e:
            messagebox.showerror("Copy Error", str(e), parent=self.history_window)

    def _on_click_image(self, pil_img, frame):
        try:
            set_clipboard_image(pil_img)
            self._pulse_green(frame)
            if settings.get("autoclose", False):
                if self.history_window:
                    self.history_window.destroy()
                    self.history_window = None
        except Exception as e:
            messagebox.showerror("Copy Error", str(e), parent=self.history_window)

    def _copy_item_button(self, item, frame):
        # if we had copy buttons earlier; left here if needed
        if item["type"] == "text":
            pyperclip.copy(item["content"])
        else:
            set_clipboard_image(item["content"])
        self._pulse_green(frame)

    def _pulse_green(self, frame):
        # pronounced green flash on border then fade back to gray
        steps = 14
        def animate(i=0):
            if i <= steps:
                t = i/steps
                # ease function
                ease = 3*t*t - 2*t*t*t
                # start white-ish -> green -> gray
                # use green intensity peaking early for pronounced effect
                peak = 1 - abs(ease - 0.4) / 0.6
                # compute color between white and green
                gw = [int(SUCCESS[1:3], 16), int(SUCCESS[3:5], 16), int(SUCCESS[5:7], 16)]
                base = (68,68,68)  # gray border base
                r = int(base[0] + (gw[0]-base[0]) * peak)
                g = int(base[1] + (gw[1]-base[1]) * peak)
                b = int(base[2] + (gw[2]-base[2]) * peak)

                s = ttk.Style()
                s.configure("Success.TFrame", background=f"#{r:02x}{g:02x}{b:02x}")
                frame.configure(style="Success.TFrame")

                frame.after(18, lambda: animate(i+1))
            else:
                frame.configure(style="TFrame")
        animate(0)

    # ---------------- history manipulation
    def _push_history(self, item):
        # dedupe
        if item["type"] == "text":
            if any(x["type"] == "text" and x["content"] == item["content"] for x in self.history):
                return
        self.history.insert(0, item)
        self.history = self.history[:self.history_limit]
        # add widget at top with animation
        if self.history_window and self.history_window.winfo_exists():
            # insert at top visually: place before first child
            # tkinter pack only supports pack before, so we'll rebuild quickly
            # simpler: rebuild all (acceptable for modest sizes)
            self._rebuild_history_with_new_top(item)

    def _rebuild_history_with_new_top(self, new_item):
        # rebuild all but animate the first (new) with fade.
        # Capture current items
        items = list(self.history)
        # clear
        for w in self.scrollable_frame.winfo_children():
            w.destroy()
        # insert new first with animation=true
        self._insert_history_item_widget(items[0], animate=True)
        # insert the rest without animation
        for it in items[1:]:
            self._insert_history_item_widget(it, animate=False)

    # ---------------- polling clipboard
    def _start_polling(self):
        def loop():
            while True:
                try:
                    # prefer image
                    img = ImageGrab.grabclipboard()
                    if isinstance(img, Image.Image):
                        # crude bytes compare
                        try:
                            buf = io.BytesIO()
                            img.convert("RGB").save(buf, "PNG")
                            bytes_now = buf.getvalue()
                            same = False
                            if isinstance(self.last_clipboard, bytes):
                                same = (bytes_now == self.last_clipboard)
                            if not same:
                                self.last_clipboard = bytes_now
                                self._push_history({"type":"image","content":img.copy(),"ts":time.time()})
                        except Exception:
                            pass
                    else:
                        try:
                            txt = pyperclip.paste()
                        except Exception:
                            txt = None
                        if txt and txt != self.last_clipboard:
                            self.last_clipboard = txt
                            self._push_history({"type":"text","content":txt,"ts":time.time()})
                except Exception:
                    pass
                time.sleep(0.9)
        t = threading.Thread(target=loop, daemon=True)
        t.start()

    # ---------------- hotkey management
    def _register_hotkey(self, hotkey):
        try:
            keyboard.remove_all_hotkeys()
        except Exception:
            pass
        try:
            keyboard.add_hotkey(hotkey, lambda: self.root.after(0, self.show_main_window))
            settings["hotkey"] = hotkey
            save_settings(settings)
        except Exception as e:
            print("Failed to register hotkey:", e)

    def _open_hotkey_dialog(self):
        if not (self.history_window and self.history_window.winfo_exists()):
            return
        dlg = tk.Toplevel(self.history_window)
        dlg.title("Change Hotkey")
        dlg.geometry("340x140")
        dlg.configure(bg=BG_SECONDARY)
        dlg.resizable(False, False)
        ttk.Label(dlg, text="Enter new hotkey (e.g. ctrl+alt+h):", background=BG_SECONDARY, foreground=TEXT_PRIMARY, font=FONT_NORMAL).pack(padx=12, pady=(12,6))
        ent = ttk.Entry(dlg)
        ent.insert(0, settings.get("hotkey", DEFAULTS["hotkey"]))
        ent.pack(fill="x", padx=12)
        def save():
            hk = ent.get().strip().lower()
            if hk:
                self._register_hotkey(hk)
                self.hotkey_display.config(text=hk)
                dlg.destroy()
                messagebox.showinfo("Hotkey Changed", f"Hotkey set to {hk}", parent=self.history_window)
        ttk.Button(dlg, text="Save", command=save, style="TButton").pack(pady=12)

    # ---------------- save history
    def _save_history(self):
        folder = save_history_folder(self.history)
        try:
            os.startfile(folder)
        except Exception:
            pass
        messagebox.showinfo("Saved", f"Saved history to {folder}", parent=self.history_window)

    # ---------------- tray icon
    def _setup_tray(self):
        def on_quit(icon, item):
            try:
                icon.stop()
            except Exception:
                pass
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
            try:
                self.root.quit()
                self.root.destroy()
            except Exception:
                pass
            sys.exit(0)
        def on_show(icon, item):
            self.root.after(0, self.show_main_window)

        # load image if present
        img_path = None
        if os.path.exists("icon.png"):
            img_path = "icon.png"
        elif os.path.exists("icon.ico"):
            img_path = "icon.ico"

        if img_path:
            try:
                tray_img = Image.open(img_path).convert("RGBA").resize((64,64), Image.LANCZOS)
            except Exception:
                tray_img = Image.new("RGBA", (64,64), (0,0,0,255))
        else:
            tray_img = Image.new("RGBA", (64,64), (0,0,0,255))

        menu = (TrayItem("Show/Hide", on_show), TrayItem("Quit", on_quit))
        self.tray_icon = TrayIcon("Null Clipboard", tray_img, "Null Clipboard", menu)
        t = threading.Thread(target=self.tray_icon.run, daemon=True)
        t.start()

    # ---------------- window close/hide
    def _on_window_close(self):
        # hide instead of destroy so tray still controls app
        try:
            if self.history_window:
                self.history_window.withdraw()
        except Exception:
            pass

# ---------------------------
# Run the app
# ---------------------------
if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    app = NullClipboardApp(root)
    # ensure main window shows at least once
    app.show_main_window()
    root.mainloop()
