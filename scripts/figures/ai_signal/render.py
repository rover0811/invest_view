"""docs/design/figures에 AI 시그널 시나리오의 MP4와 GIF를 만든다. 실행: python scripts/figures/ai_signal/render.py"""
import subprocess
from pathlib import Path

from matplotlib.animation import FFMpegWriter, FuncAnimation

from PIL import Image

from scenario import build_timeline, draw, fig

OUT = Path(__file__).resolve().parents[3] / "docs" / "design" / "figures"
MP4 = OUT / "ai-signal-scenario.mp4"
GIF = OUT / "ai-signal-scenario.gif"
KEY_SCENES = {3: 610, 5: 713, 8: 755, 9: 788}


def render_key_scenes():
    paths = []
    for no, minute in KEY_SCENES.items():
        draw(minute)
        path = OUT / f"ai-signal-scene-{no}.png"
        fig.savefig(path, dpi=200, facecolor="white")
        paths.append(path)
    tiles = [Image.open(p).convert("RGB") for p in paths]
    w, h = tiles[0].size
    grid = Image.new("RGB", (w * 2 + 40, h * 2 + 40), "white")
    for i, tile in enumerate(tiles):
        grid.paste(tile, ((i % 2) * (w + 40), (i // 2) * (h + 40)))
    grid.save(OUT / "ai-signal-scenes-grid.png", optimize=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    writer = FFMpegWriter(fps=24, codec="libx264", extra_args=[
        "-vf", "scale=1920:1080:flags=lanczos", "-pix_fmt", "yuv420p", "-crf", "18",
        "-preset", "slow", "-movflags", "+faststart"])
    FuncAnimation(fig, draw, frames=build_timeline(fps=24), blit=False).save(MP4, writer=writer, dpi=150)
    subprocess.run([
        "ffmpeg", "-loglevel", "error", "-y", "-i", str(MP4), "-vf",
        "fps=12,scale=1560:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
        "[b][p]paletteuse=dither=none:diff_mode=rectangle", str(GIF)], check=True)
    render_key_scenes()


if __name__ == "__main__":
    main()
