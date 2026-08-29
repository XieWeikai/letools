# Third-party notices

Linux x86-64 wheels built with the `video` feature bundle FFmpeg 8.0.3 shared
libraries. FFmpeg is licensed under the GNU Lesser General Public License
version 2.1 or later for the configuration used by letools. The build disables
GPL and nonfree components and enables only the libraries, demuxers, muxer, and
file protocol required for packet-copy operations.

- Source: https://ffmpeg.org/releases/ffmpeg-8.0.3.tar.xz
- Source SHA-256: `6136812ea6d4e68bdba27e33c2a94382711cdf4f8602ffef056ff792bd6f9818`
- License text: https://ffmpeg.org/legal.html
- Exact build configuration: `scripts/build_ffmpeg_linux.sh` in the matching
  letools source tag

Recipients may replace the bundled compatible FFmpeg shared libraries under
the terms of the LGPL. letools does not enable GPL or nonfree FFmpeg features.
