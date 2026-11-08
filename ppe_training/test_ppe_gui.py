"""Simple local image tester for the trained VisionWorks PPE model."""

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "ppe_training" / "runs" / "ppe_yolov8m_v1" / "weights" / "best.pt"


class PpeTester(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VisionWorks PPE Detection Tester")
        self.geometry("1000x760")
        self.model = None
        self.image = None
        self.rendered = None
        self.conf = tk.DoubleVar(value=0.35)
        self.status = tk.StringVar(value="Upload an image to start.")
        top = ttk.Frame(self, padding=12)
        top.pack(fill=tk.X)
        ttk.Label(top, text="VisionWorks PPE Detection",
                  font=("Segoe UI", 18, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="Upload Image", command=self.choose).pack(side=tk.RIGHT)
        controls = ttk.Frame(self, padding=12)
        controls.pack(fill=tk.X)
        ttk.Label(controls, text="Confidence:").pack(side=tk.LEFT)
        ttk.Scale(controls, from_=0.1, to=0.9, variable=self.conf,
                  orient=tk.HORIZONTAL, length=220).pack(side=tk.LEFT, padx=8)
        self.label = ttk.Label(controls, text="0.35")
        self.label.pack(side=tk.LEFT)
        self.conf.trace_add("write", lambda *_: self.label.configure(
            text=f"{self.conf.get():.2f}"))
        self.canvas = tk.Canvas(self, background="#171717", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)
        self.canvas.bind("<Configure>", lambda _: self.render())
        ttk.Label(self, textvariable=self.status, padding=12).pack(anchor=tk.W)

    def choose(self):
        path = filedialog.askopenfilename(
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp")])
        if not path:
            return
        try:
            if self.model is None:
                if not MODEL_PATH.is_file():
                    raise FileNotFoundError(f"Model not found: {MODEL_PATH}")
                self.model = YOLO(str(MODEL_PATH))
            result = self.model.predict(path, conf=float(self.conf.get()),
                                        device=0, verbose=False)[0]
            self.image = Image.fromarray(result.plot()[:, :, ::-1])
            count = len(result.boxes) if result.boxes is not None else 0
            self.status.set(f"{Path(path).name} | {count} detection(s)")
            self.render()
        except Exception as error:
            messagebox.showerror("Detection failed", str(error))

    def render(self):
        if self.image is None:
            return
        image = self.image.copy()
        image.thumbnail((max(1, self.canvas.winfo_width() - 20),
                         max(1, self.canvas.winfo_height() - 20)))
        self.rendered = ImageTk.PhotoImage(image)
        self.canvas.delete("all")
        self.canvas.create_image(self.canvas.winfo_width() // 2,
                                 self.canvas.winfo_height() // 2,
                                 image=self.rendered)


if __name__ == "__main__":
    PpeTester().mainloop()
