"""A stand-in for tkinter in the browser (Pyodide has no Tk): the trainer's modules import
and run without a window. Everything is a do-nothing object, except a Canvas, which keeps
the text drawn on it, so the ICM Coach's text can be read back and shown in the page."""

import sys
import types


class Dummy:
    """Accepts any call and any attribute; returns itself."""

    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return self

    def __getattr__(self, name):
        return self

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def winfo_screenheight(self):
        return 100000

    def winfo_exists(self):
        return False


class Canvas(Dummy):
    """Keeps every text item: (text, colour, font) in drawing order."""

    def __init__(self, *a, **k):
        self.items = []

    def create_text(self, x, y, text="", fill=None, font=None, width=None, **k):
        self.items.append({"text": text, "fill": fill, "font": font, "x": x, "y": y})
        return len(self.items)

    def bbox(self, item):
        it = self.items[item - 1]
        lines = 1 + len(it["text"]) // 90 + it["text"].count("\n")
        return (0, it["y"], 600, it["y"] + 16 * lines)

    def find_all(self):
        return list(range(1, len(self.items) + 1))

    def type(self, i):
        return "text"

    def itemcget(self, i, opt):
        return self.items[i - 1].get(opt)


class Toplevel(Dummy):
    last = None

    def __init__(self, *a, **k):
        self.children_ = []
        Toplevel.last = self

    def winfo_children(self):
        return self.children_


def _canvas_in(parent, *a, **k):
    cv = Canvas()
    if isinstance(parent, Toplevel):
        parent.children_.append(cv)
    return cv


class _Font(Dummy):
    def measure(self, text):
        return 7 * len(str(text))

    def metrics(self, *a):
        return 14


class TclError(Exception):
    pass


def install():
    """Put the stand-in in place of tkinter (only when there's no real one)."""
    try:
        import tkinter  # noqa: F401
        return False
    except ImportError:
        pass
    tk = types.ModuleType("tkinter")
    tk.__getattr__ = lambda name: Dummy
    tk.Tk = Dummy
    tk.Toplevel = Toplevel
    tk.Canvas = _canvas_in
    tk.TclError = TclError
    tk.PhotoImage = Dummy
    font = types.ModuleType("tkinter.font")
    font.Font = _Font
    font.families = lambda *a, **k: ()
    font.__getattr__ = lambda name: Dummy
    tk.font = font
    sys.modules["tkinter"] = tk
    sys.modules["tkinter.font"] = font
    return True
