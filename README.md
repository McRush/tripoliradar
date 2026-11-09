# TripoliRadar: modern renderer for F-19 Stealth Fighter

[![TripoliRadar demo](https://img.youtube.com/vi/R2ad7ow__2w/maxresdefault.jpg)](https://youtu.be/R2ad7ow__2w)

*Click the image to watch the demo on YouTube.*

A high-resolution renderer for MicroProse's **F-19 Stealth Fighter** (1988). The original game keeps running in
DOSBox and handles the flight model, the AI and the missions. The viewer reads the game state in real time and
renders the same world in high resolution.

**You need your own copy of the game.** This repository contains no game files. Current release supports Steam version of F-19.

## Usage

1. Unpack the release archive (or copy the contents of this repository) into a folder inside your F-19 installation.
2. Run `TripoliRadar.exe` or `tripoliradar.py`. The first start takes several seconds while it checks the game
   resources. If you see the main menu, everything is working.
3. Start F-19 Stealth Fighter in DOSBox and begin any mission.
4. Once you are on the runway, switch to the TripoliRadar window and choose "Live game" from the main menu.

The Replays, World and Models modes do not need DOSBox to be running.

## Features

- **Frame interpolation.** The original game runs at about 16 FPS. The renderer stays one frame behind the game,
  which lets it show smooth motion at any frame rate.
- **Replays.** Every flight is saved from start to finish. You can watch replays with a free camera.
- **World exploration mode.**

## How it works

- **Live game state.** TripoliRadar reads the game state from DOSBox's memory while the game is running.
- **Original assets.** Models, scenery and maps are loaded straight from the game's own files.
- **A very large world.** The game world is 2 million feet (about 600 km) across. To keep distant objects from
  flickering, the renderer is camera-relative and uses a reversed-Z, 32-bit floating-point depth buffer.

## FAQ

**Q: Why Python?**

A: No particular reason, just for fun.

**Q: Why not make a full recompilation that doesn't need DOSBox?**

A: Originally I only planned to make a world viewer. A full recompilation is out of scope for now.

**Q: Are you planning to develop this project further?**

A: I consider it finished. I may fix bugs or critical vulnerabilities, but I don't plan to put much more effort into
it, and I don't check the repository often. If you want to add a lot of new functionality, consider forking.

## Acknowledgements

The renderer is based on [3D-Graphics-Engine](https://github.com/StanislavPetrovV/3D-Graphics-Engine) by
StanislavPetrovV ([Coder Space on YouTube](https://www.youtube.com/channel/UCwe6kcllhainHICL1vnq41g)). I spent some
time developing the engine further from the Coder Space example: a deferred pipeline, a dynamic sky, point lights and
more. I later removed most of those features, because the game uses no shading beyond its 16-color EGA palette.

**Claude Opus 5.5** did the whole investigation of the 3D assets and the game world, the DOSBox state reading and
most of the coding.

Music in the demo: “F19 SIMULATOR 2026 (MAIN THEME)” by Silenci si us plau (Àngel Martínez Domingo),
[YouTube channel](https://www.youtube.com/channel/UCi5BZx1LhEiGM33Prl3gO2w).

The project also uses:

- [Cozette](https://github.com/the-moonwitch/Cozette) bitmap font (I absolutely love it)
- [Yale Bright Star Catalogue](http://tdc-www.harvard.edu/catalogs/bsc5.html) (BSC5), for the night sky
- [Blue noise texture](https://momentsingraphics.de/BlueNoise.html) by Christoph Peters
- [pygame-ce](https://github.com/pygame-community/pygame-ce)
- [ModernGL](https://github.com/moderngl/moderngl)
- [PyOpenGL](https://github.com/mcfletch/pyopengl) (because ModernGL somehow can't create a 32-bit depth buffer by itself)
- [PyGLM](https://github.com/Zuzu-Typ/PyGLM)
- [NumPy](https://numpy.org)
- [PyInstaller](https://pyinstaller.org), for the Windows build

## Disclaimer

This software is provided "as is", without warranty of any kind. Use it at your own risk. The author is not
responsible for any damage, data loss or other problems that may result from using it. See [LICENSE](LICENSE) for
the full terms.

## License

MIT, see [LICENSE](LICENSE). Third-party parts (font, textures, engine base code) and the libraries packed into
the Windows build are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

F-19 Stealth Fighter is © 1988 MicroProse. This project is not affiliated with or endorsed by its rights holders.
