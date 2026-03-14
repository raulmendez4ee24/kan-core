from __future__ import annotations

import math
import os
import tkinter as tk
from typing import Tuple

from brain.jarvis_ui_state import publish_state
from brain.jarvis_ui_state import read_state


def _hex_to_rgb(color: str) -> Tuple[int, int, int]:
    token = str(color or "#000000").strip().lstrip("#")
    if len(token) != 6:
        return (0, 0, 0)
    return (int(token[0:2], 16), int(token[2:4], 16), int(token[4:6], 16))


def _rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, int(rgb[0]))),
        max(0, min(255, int(rgb[1]))),
        max(0, min(255, int(rgb[2]))),
    )


def _mix(a: str, b: str, ratio: float) -> str:
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    t = max(0.0, min(1.0, float(ratio)))
    return _rgb_to_hex(
        (
            ar + ((br - ar) * t),
            ag + ((bg - ag) * t),
            ab + ((bb - ab) * t),
        )
    )


class JarvisOrbUI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("K'an Orb")
        self.root.geometry("260x300+40+80")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#070f1d")

        self.tick_ms = max(20, int(str(os.getenv("JARVIS_UI_TICK_MS") or "45")))
        self.width = 260
        self.height = 300
        self.center_x = self.width // 2
        self.center_y = 130
        self.mode = "standby"
        self.level = 0.2
        self.text = ""
        self.spin = 0.0
        self.time_step = 0.0
        self.drag_x = 0
        self.drag_y = 0

        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg="#070f1d",
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.status_label = tk.Label(
            self.root,
            text="standby",
            fg="#a9c7ff",
            bg="#070f1d",
            font=("Menlo", 11, "bold"),
        )
        self.status_label.place(x=20, y=255)

        self.subtitle_label = tk.Label(
            self.root,
            text="K'an online",
            fg="#6f8cbf",
            bg="#070f1d",
            font=("Menlo", 9),
            anchor="w",
            justify="left",
        )
        self.subtitle_label.place(x=20, y=275)

        self.root.bind("<ButtonPress-1>", self._on_press)
        self.root.bind("<B1-Motion>", self._on_drag)
        self.root.bind("<Escape>", lambda _event: self.root.destroy())
        self.root.after(self.tick_ms, self._tick)

    def _on_press(self, event: tk.Event) -> None:
        self.drag_x = int(event.x)
        self.drag_y = int(event.y)

    def _on_drag(self, event: tk.Event) -> None:
        x = int(self.root.winfo_x() + event.x - self.drag_x)
        y = int(self.root.winfo_y() + event.y - self.drag_y)
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")

    def _pull_state(self) -> None:
        state = read_state()
        mode = str(state.get("mode") or "standby").strip().lower()
        if mode not in {"standby", "listening", "thinking", "speaking"}:
            mode = "standby"
        level = state.get("level")
        try:
            numeric_level = float(level) if level is not None else 0.0
        except (TypeError, ValueError):
            numeric_level = 0.0
        self.level = max(0.0, min(1.0, numeric_level))
        self.mode = mode
        self.text = str(state.get("text") or "").strip()[:50]

    def _base_color(self) -> str:
        if self.mode == "listening":
            return "#ff9a3c"
        if self.mode == "thinking":
            return "#84f1ff"
        if self.mode == "speaking":
            return "#9af2ff"
        return "#3c78ff"

    def _radius(self) -> float:
        pulse = math.sin(self.time_step)
        if self.mode == "listening":
            return 48.0 + (pulse * 11.0)
        if self.mode == "thinking":
            return 46.0 + (pulse * 3.0)
        if self.mode == "speaking":
            return 45.0 + (self.level * 22.0) + (pulse * 3.0)
        return 44.0 + (pulse * 4.0)

    def _draw_background(self) -> None:
        self.canvas.delete("all")
        ring_colors = ["#0d1a33", "#0f213f", "#123056"]
        radii = [112, 98, 84]
        for radius, color in zip(radii, ring_colors):
            self.canvas.create_oval(
                self.center_x - radius,
                self.center_y - radius,
                self.center_x + radius,
                self.center_y + radius,
                fill=color,
                outline="",
            )

    def _draw_orb(self) -> None:
        base = self._base_color()
        radius = self._radius()
        halo_1 = _mix(base, "#ffffff", 0.4)
        halo_2 = _mix(base, "#000000", 0.3)

        for factor, width, color in ((1.85, 2, halo_2), (1.55, 2, halo_1), (1.22, 2, base)):
            r = radius * factor
            self.canvas.create_oval(
                self.center_x - r,
                self.center_y - r,
                self.center_x + r,
                self.center_y + r,
                outline=color,
                width=width,
            )

        inner = _mix(base, "#ffffff", 0.25 if self.mode != "speaking" else 0.45)
        self.canvas.create_oval(
            self.center_x - radius,
            self.center_y - radius,
            self.center_x + radius,
            self.center_y + radius,
            fill=inner,
            outline=_mix(base, "#000000", 0.2),
            width=2,
        )

        core = radius * 0.55
        self.canvas.create_oval(
            self.center_x - core,
            self.center_y - core,
            self.center_x + core,
            self.center_y + core,
            fill=_mix(base, "#ffffff", 0.55),
            outline="",
        )

        if self.mode == "thinking":
            arc_r = radius * 1.45
            self.spin = (self.spin + 14.0) % 360.0
            self.canvas.create_arc(
                self.center_x - arc_r,
                self.center_y - arc_r,
                self.center_x + arc_r,
                self.center_y + arc_r,
                start=self.spin,
                extent=140,
                style="arc",
                outline="#ffffff",
                width=4,
            )

    def _draw_labels(self) -> None:
        self.status_label.configure(text=self.mode)
        if self.text:
            self.subtitle_label.configure(text=self.text)
        elif self.mode == "listening":
            self.subtitle_label.configure(text="wake-word detected")
        elif self.mode == "thinking":
            self.subtitle_label.configure(text="executing goal...")
        elif self.mode == "speaking":
            self.subtitle_label.configure(text="tts output")
        else:
            self.subtitle_label.configure(text="K'an online")

    def _tick(self) -> None:
        self._pull_state()
        if self.mode == "speaking":
            self.time_step += 0.5
        elif self.mode == "thinking":
            self.time_step += 0.26
        elif self.mode == "listening":
            self.time_step += 0.35
        else:
            self.time_step += 0.12
        self._draw_background()
        self._draw_orb()
        self._draw_labels()
        self.root.after(self.tick_ms, self._tick)

    def run(self) -> None:
        publish_state("standby", source="gui", text="ui_boot", level=0.15)
        self.root.mainloop()


def main() -> None:
    ui = JarvisOrbUI()
    ui.run()


if __name__ == "__main__":
    main()

