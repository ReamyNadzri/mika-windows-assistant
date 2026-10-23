"""Render bounded chart/diagram specifications using trusted code only."""
import math
import threading
from pathlib import Path

from .files import digest

_RENDER_LOCK = threading.Lock()


class VisualService:
    def __init__(self, policy):
        self.policy = policy

    def render(self, path, spec):
        created = False
        target = None
        try:
            target = self.policy.resolve(path)
            if target.suffix.lower() != ".png":
                raise ValueError("Visual outputs must be .png files")
            kind = spec.get("kind")
            if kind not in {"bar", "line", "pie", "diagram"}:
                raise ValueError("Use bar, line, pie, or diagram; executable specifications are not supported")
            labels = spec.get("labels", [])
            if not isinstance(labels, list) or not 1 <= len(labels) <= (30 if kind == "diagram" else 200):
                raise ValueError("Provide 1-200 chart labels or 1-30 diagram labels")
            labels = [str(label)[:120].replace("$", "") for label in labels]
            values = spec.get("values", [])
            if kind != "diagram":
                if len(values) != len(labels) or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
                    raise ValueError("Provide one finite numeric value per label")
                if kind == "pie" and (any(v < 0 for v in values) or sum(values) <= 0):
                    raise ValueError("Pie values must be nonnegative with a positive total")
            with _RENDER_LOCK:
                from matplotlib.figure import Figure
                figure = Figure(figsize=(10, 6), dpi=150, layout="constrained")
                axes = figure.subplots()
                if kind == "bar":
                    axes.bar(labels, values, color="#267caa")
                elif kind == "line":
                    axes.plot(labels, values, marker="o", color="#267caa")
                elif kind == "pie":
                    axes.pie(values, labels=labels, autopct="%1.1f%%")
                else:
                    axes.set_axis_off()
                    for index, label in enumerate(labels):
                        y = 1 - (index + 1) / (len(labels) + 1)
                        axes.text(.5, y, label, ha="center", va="center", bbox={"boxstyle": "round,pad=0.5", "fc": "#e7f3fa", "ec": "#267caa"}, fontsize=max(6, 13 - len(labels) // 4))
                        if index:
                            previous = 1 - index / (len(labels) + 1)
                            axes.annotate("", xy=(.5, y + .025), xytext=(.5, previous - .025), arrowprops={"arrowstyle": "->", "color": "#267caa"})
                axes.set_title(str(spec.get("title", ""))[:200].replace("$", ""))
                with target.open("xb") as output:
                    created = True
                    figure.savefig(output, format="png")
                from PIL import Image
                with Image.open(target) as image:
                    image.verify()
            return {"ok": True, "path": str(target), "sha256": digest(target), "evidence": "PNG rendered and decoded successfully", "artifacts": [str(target)]}
        except (OSError, ValueError, TypeError) as exc:
            if created and target is not None and target.exists():
                target.unlink()
            return {"ok": False, "error": str(exc)}
