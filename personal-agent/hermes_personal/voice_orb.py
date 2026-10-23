"""Transparent, non-activating Windows speaking orb. All Tk calls stay on its thread.

The orb is a frosted-glass sphere rendered with numpy into a per-pixel-alpha layered
window (UpdateLayeredWindow), so edges, glow and shadow blend smoothly with the desktop.
"""
import ctypes
import math
import time
from ctypes import wintypes

BASE = 240  # logical window size; scaled by the monitor DPI
# Fluid colours inside the glass: (rgb, orbit radius, speed, phase).
BLOBS = (((0.32, 0.90, 1.00), 0.34, 0.9, 0.0),
         ((0.33, 0.45, 1.00), 0.40, -0.7, 2.1),
         ((0.72, 0.42, 1.00), 0.36, 1.1, 4.0),
         ((1.00, 0.42, 0.72), 0.30, -1.3, 5.2))


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
                ("bmiColors", wintypes.DWORD * 3)]


class _Blend(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


def _smooth(edge0, edge1, x):
    import numpy as np
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _over(color, alpha, top_color, top_alpha):
    """Premultiplied 'over' composite, in place."""
    color *= (1 - top_alpha)[..., None]
    color += top_color
    alpha *= 1 - top_alpha
    alpha += top_alpha


def _tint(color, alpha, rgb, top_alpha):
    """Composite a flat colour layer with the given coverage, in place."""
    import numpy as np
    _over(color, alpha, top_alpha[..., None] * np.array(rgb, np.float32), top_alpha)


class SpeakingOrb:
    def __init__(self, root):
        import numpy as np

        self.root = root
        self.audio, self.rate, self.started, self.level = None, 24000, 0, 0.0
        root.withdraw()
        root.title("Mika speaking")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.update_idletasks()
        user = self.user = ctypes.windll.user32
        user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user.GetAncestor.restype = ctypes.c_void_p
        self.hwnd = user.GetAncestor(root.winfo_id(), 2) or root.winfo_id()
        try:
            user.GetDpiForWindow.argtypes = [ctypes.c_void_p]
            scale = max(1.0, user.GetDpiForWindow(self.hwnd) / 96)
        except (AttributeError, OSError):
            scale = 1.0
        size = self.size = round(BASE * scale)
        self.unit = size / BASE
        x, y = max(0, root.winfo_screenwidth() - size - round(26 * scale)), \
            max(0, root.winfo_screenheight() - size - round(90 * scale))
        root.geometry(f"{size}x{size}+{x}+{y}")
        root.update_idletasks()
        get_style = user.GetWindowLongW
        set_style = user.SetWindowLongW
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        style = get_style(self.hwnd, -20)
        # Layered + transparent hit testing + no taskbar entry + no activation.
        set_style(self.hwnd, -20, style | 0x80000 | 0x20 | 0x80 | 0x08000000)
        self._setup_surface()
        self._setup_geometry()
        self.root.after(33, self._animate)

    def _setup_geometry(self):
        """Pixel grids that stay fixed while the orb only changes radius and fluid."""
        import numpy as np

        axis = np.arange(self.size, dtype=np.float32) + 0.5
        cx, cy, reach = self.size / 2, 104 * self.unit, 80 * self.unit
        self.ax, self.ay = axis - cx, axis - cy
        # Everything but the shadow and label lives inside this box around the sphere.
        self.box = (slice(max(0, int(cy - reach)), int(cy + reach) + 1),
                    slice(max(0, int(cx - reach)), int(cx + reach) + 1))
        self.bx, self.by = self.ax[self.box[1]], self.ay[self.box[0]]
        dx, dy = self.bx[None, :], self.by[:, None]
        self.dist = np.hypot(dx, dy)
        self.facing = (dx * -0.5 + dy * -0.85) / np.maximum(self.dist, 1)
        self.label = self._render_label()

    def _setup_surface(self):
        gdi, user = ctypes.windll.gdi32, self.user
        user.GetDC.restype = ctypes.c_void_p
        gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                         ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint]
        gdi.CreateDIBSection.restype = ctypes.c_void_p
        gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user.UpdateLayeredWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        info = _BitmapInfo()
        info.biSize = ctypes.sizeof(_BitmapInfo) - ctypes.sizeof(wintypes.DWORD * 3)
        info.biWidth, info.biHeight = self.size, -self.size  # top-down rows
        info.biPlanes, info.biBitCount = 1, 32
        self.screen_dc = user.GetDC(None)
        self.memory_dc = gdi.CreateCompatibleDC(self.screen_dc)
        self.bits = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(self.memory_dc, ctypes.byref(info), 0, ctypes.byref(self.bits), None, 0)
        gdi.SelectObject(self.memory_dc, bitmap)
        self.blend = _Blend(0, 0, 255, 1)  # AC_SRC_OVER with per-pixel AC_SRC_ALPHA

    def _render_label(self):
        """Frosted name pill under the orb, premultiplied float RGBA."""
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont

        ss, u = 4, self.unit
        image = Image.new("RGBA", (self.size * ss, self.size * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("seguisb.ttf", round(12.5 * u * ss))
        except OSError:
            font = ImageFont.load_default()
        width, top = 62 * u * ss, 201 * u * ss
        left = self.size * ss / 2 - width / 2
        box = (left, top, left + width, top + 24 * u * ss)
        draw.rounded_rectangle(box, radius=12 * u * ss, fill=(18, 24, 36, 150),
                               outline=(255, 255, 255, 90), width=max(1, round(u * ss)))
        draw.text((self.size * ss / 2, top + 12 * u * ss), "Mika", font=font,
                  fill=(240, 248, 255, 245), anchor="mm")
        pixels = np.asarray(image.resize((self.size, self.size), Image.LANCZOS), dtype=np.float32) / 255
        alpha = pixels[..., 3]
        return pixels[..., :3] * alpha[..., None], alpha

    def show(self, audio, rate):
        self.audio, self.rate, self.started, self.level = audio, rate, time.monotonic(), 0.0
        self._draw(0.1)
        self.root.deiconify()
        self._draw(0.1)

    def hide(self):
        self.audio = None
        self.root.withdraw()

    def _frame(self, energy):
        """Render one premultiplied RGBA frame as float arrays."""
        import numpy as np

        u, ax, ay, dist = self.unit, self.bx, self.by, self.dist
        elapsed = time.monotonic() - self.started
        breath = (math.sin(elapsed * 2.4) + 1) / 2
        radius = (70 + 6 * energy + 1.5 * breath) * u
        rr = dist / radius
        disc = _smooth(radius + 0.8, radius - 0.8, dist)
        color = np.zeros((self.size, self.size, 3), np.float32)
        # Soft contact shadow below the sphere (gaussians are separable: outer products).
        alpha = 0.30 * np.outer(np.exp(-2.2 * ((self.ay - radius * 0.86) / (radius * 0.2)) ** 2),
                                np.exp(-2.2 * (self.ax / (radius * 0.8)) ** 2)).astype(np.float32)
        sphere, cover = color[self.box], alpha[self.box]

        # Deep tinted glass body so the fluid glows on any wallpaper.
        _tint(sphere, cover, (0.03, 0.06, 0.12), disc * (0.62 - 0.12 * rr ** 2))
        # Fluid: drifting colour blobs that swell with the voice.
        glow = np.zeros_like(sphere)
        weight = np.zeros_like(cover)
        spread = radius * (0.42 + 0.22 * energy)
        for rgb, orbit, speed, phase in BLOBS:
            angle = elapsed * speed + phase
            bx, by = radius * orbit * math.cos(angle), radius * orbit * math.sin(angle * 1.3)
            blob = np.outer(np.exp(-((ay - by) / spread) ** 2), np.exp(-((ax - bx) / spread) ** 2))
            glow += np.array(rgb, np.float32) * blob[..., None]
            weight += blob
        core_width = radius * (0.22 + 0.18 * energy)
        core = (0.25 + 0.75 * energy) * np.outer(np.exp(-(ay / core_width) ** 2), np.exp(-(ax / core_width) ** 2))
        glow += core[..., None]
        weight += core
        fluid_alpha = np.clip(weight, 0, 1) * 0.92 * _smooth(radius, radius * 0.7, dist)
        hue = np.clip(glow / np.maximum(weight, 1e-4)[..., None], 0, 1)
        _over(sphere, cover, hue * fluid_alpha[..., None], fluid_alpha)

        # Fresnel frost: the glass turns milky towards its edge.
        _tint(sphere, cover, (0.88, 0.95, 1.0), disc * 0.3 * _smooth(0.7, 1.0, rr) ** 1.8)
        # Rim light: bright from the top-left, a cool refracted glint bottom-right.
        ring = np.exp(-((dist - radius + 1.3 * u) / (1.2 * u)) ** 2)
        _tint(sphere, cover, (1, 1, 1), ring * (0.22 + 0.7 * np.clip(self.facing, 0, 1)))
        _tint(sphere, cover, (0.55, 0.92, 1.0), ring * 0.55 * np.clip(-self.facing, 0, 1) ** 2)
        # Specular highlight: a soft elliptical sheen across the top, and a small low glint.
        hx, hy = (ax[None, :] + radius * 0.12) / (radius * 0.6), (ay[:, None] + radius * 0.56) / (radius * 0.27)
        sheen = disc * 0.62 * _smooth(1.0, 0.45, np.hypot(hx, hy)) * _smooth(0.9, -0.6, hy) ** 0.6
        _tint(sphere, cover, (1, 1, 1), sheen)
        _tint(sphere, cover, (1, 1, 1), disc * 0.3 * _smooth(1.0, 0.0, np.hypot(
            (ax[None, :] - radius * 0.42) / (radius * 0.16), (ay[:, None] - radius * 0.6) / (radius * 0.08))))

        _over(color, alpha, *self.label)
        return color, alpha

    def _draw(self, energy):
        import numpy as np

        self.level += (energy - self.level) * 0.35
        color, alpha = self._frame(self.level)
        bgra = np.empty((self.size, self.size, 4), np.uint8)
        bgra[..., :3] = (np.clip(color[..., ::-1], 0, 1) * 255 + 0.5).astype(np.uint8)
        bgra[..., 3] = (np.clip(alpha, 0, 1) * 255 + 0.5).astype(np.uint8)
        ctypes.memmove(self.bits, bgra.ctypes.data, bgra.nbytes)
        size, origin = wintypes.SIZE(self.size, self.size), wintypes.POINT(0, 0)
        self.user.UpdateLayeredWindow(self.hwnd, self.screen_dc, None, ctypes.byref(size), self.memory_dc,
                                      ctypes.byref(origin), 0, ctypes.byref(self.blend), 2)

    def _animate(self):
        if self.audio is not None:
            import numpy as np
            start = int((time.monotonic()-self.started) * self.rate)
            segment = self.audio[start:start+int(self.rate*0.04)]
            rms = float(np.sqrt(np.mean(segment**2))) if len(segment) else 0
            self._draw(min(1.0, rms*8))
        self.root.after(33, self._animate)
