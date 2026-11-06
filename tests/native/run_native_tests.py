#!/usr/bin/env python3
"""Build and run the firmware's PC tests. Developer tool; not needed to use the display.

  python -m pip install ziglang pillow
  python tests/native/run_native_tests.py [--ui --lovyangfx <LovyanGFX folder>]

Needs race files built by data_tool. Without --ui it runs the logic tests only.
With --ui it also runs the real UI code and saves PNG screenshots to the build folder.
"""

import argparse
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIRMWARE = ROOT / "firmware" / "F1ReplayCYD"
BUILD = HERE / "build"
REPLAY = ROOT / "data_tool" / "out" / "f1replay"
ZIG = [sys.executable, "-m", "ziglang"]
LOGIC_SOURCES = ["race_file.cpp", "manifest.cpp", "playback.cpp"]
UI_SOURCES = ["ui_common.cpp", "ui_menu.cpp", "ui_playback.cpp", "ui_setup.cpp"]
LGFX_SOURCES = ["lgfx/v1/LGFXBase.cpp", "lgfx/v1/lgfx_fonts.cpp", "lgfx/v1/lgfx_v1.cpp",
                "lgfx/v1/lgfx_v1_platforms.cpp", "lgfx/v1/lgfx_v1_panel.cpp",
                "lgfx/v1/lgfx_v1_touch.cpp"]
LGFX_C_SOURCES = ["lgfx_miniz.c", "lgfx_pngle.c", "lgfx_qoi.c", "lgfx_qrcode.c", "lgfx_tjpgd.c"]


def run(command: list) -> None:
    subprocess.run([str(part) for part in command], check=True)


def races() -> list:
    return sorted(path.name for path in (REPLAY / "races").glob("*.f1r"))


def logic_tests() -> None:
    exe = BUILD / "test_firmware.exe"
    run(ZIG + ["c++", "-std=c++17", "-O1", "-g", "-fsanitize=undefined", "-Wall", "-Wextra",
               f"-I{FIRMWARE}", HERE / "test_firmware.cpp",
               *[FIRMWARE / name for name in LOGIC_SOURCES], "-o", exe])
    run([exe, REPLAY, *races()])


def compile_object(source: Path, flags: list, out: Path) -> Path:
    if not out.exists() or out.stat().st_mtime < source.stat().st_mtime:
        if source.suffix == ".c":
            run(ZIG + ["cc", "-c", "-O1", "-w", f"-I{HERE / 'host'}", source, "-o", out])
        else:
            run(ZIG + ["c++", "-c", *flags, source, "-o", out])
    return out


def link(objects: list, exe: Path, lovyangfx_src: Path) -> None:
    """Link, with empty stand-ins for LovyanGFX's CJK fonts, which the UI never draws."""
    fonts = (lovyangfx_src / "lgfx" / "v1" / "lgfx_fonts.cpp").read_text(errors="ignore")
    names = sorted(set(re.findall(r"\blgfx_(?:font|efont)_[a-z0-9_]+", fonts)))
    stubs = BUILD / "font_stubs.c"
    lines = [f"const unsigned char {name}[1] = {{0}};" for name in names]
    stubs.write_text("\n".join(lines) + "\n")
    stub_object = BUILD / "font_stubs.o"
    run(ZIG + ["cc", "-c", stubs, "-o", stub_object])
    run(ZIG + ["c++", *objects, stub_object, "-o", exe])


def ui_preview(lovyangfx: Path) -> None:
    src = lovyangfx / "src"
    # The UI files must pick up the host display.h, not the one next to them.
    ui_dir = BUILD / "ui_src"
    ui_dir.mkdir(exist_ok=True)
    for name in UI_SOURCES:
        shutil.copy(FIRMWARE / name, ui_dir / name)
    flags = ["-std=c++17", "-O1", "-g", "-w", "-DARDUINO=100", "-DLGFX_USE_V1",
             f"-I{HERE / 'host'}", f"-I{FIRMWARE}", f"-I{src}"]
    jobs = [(src / name, BUILD / (name.replace("/", "_") + ".o")) for name in LGFX_SOURCES]
    jobs += [(src / "lgfx" / "utility" / name, BUILD / (name + ".o")) for name in LGFX_C_SOURCES]
    jobs += [(ui_dir / name, BUILD / (name + ".o")) for name in UI_SOURCES]
    jobs += [(FIRMWARE / name, BUILD / (name + ".o")) for name in LOGIC_SOURCES]
    jobs.append((HERE / "ui_preview.cpp", BUILD / "ui_preview.o"))
    with ThreadPoolExecutor(4) as pool:
        objects = list(pool.map(lambda job: compile_object(job[0], flags, job[1]), jobs))
    exe = BUILD / "ui_preview.exe"
    link(objects, exe, src)

    shots = BUILD / "screens"
    shots.mkdir(exist_ok=True)
    run([exe, shots, REPLAY, *races()])

    from PIL import Image
    for path in shots.glob("*.ppm"):
        Image.open(path).resize((960, 720), Image.NEAREST).save(path.with_suffix(".png"))
        path.unlink()
    print(f"Screenshots: {shots}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--lovyangfx", type=Path)
    args = parser.parse_args()
    BUILD.mkdir(exist_ok=True)
    logic_tests()
    if args.ui:
        ui_preview(args.lovyangfx)


if __name__ == "__main__":
    main()
