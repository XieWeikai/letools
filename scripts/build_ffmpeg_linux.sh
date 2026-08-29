#!/usr/bin/env bash
set -euo pipefail

FFMPEG_VERSION=8.0.3
FFMPEG_SHA256=6136812ea6d4e68bdba27e33c2a94382711cdf4f8602ffef056ff792bd6f9818
FFMPEG_PREFIX=${LETOOLS_FFMPEG_PREFIX:-/opt/letools-ffmpeg}
FFMPEG_WORK_ROOT=${RUNNER_TEMP:-/tmp}/letools-ffmpeg-${FFMPEG_VERSION}

build_ffmpeg() {
    if [[ -f "${FFMPEG_PREFIX}/lib/pkgconfig/libavformat.pc" ]]; then
        return
    fi

    mkdir -p "${FFMPEG_WORK_ROOT}"
    curl --fail --location --retry 5 \
        "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
        --output "${FFMPEG_WORK_ROOT}/ffmpeg.tar.xz"
    echo "${FFMPEG_SHA256}  ${FFMPEG_WORK_ROOT}/ffmpeg.tar.xz" | sha256sum --check --strict
    tar --extract --xz --file "${FFMPEG_WORK_ROOT}/ffmpeg.tar.xz" \
        --directory "${FFMPEG_WORK_ROOT}"
    (
        cd "${FFMPEG_WORK_ROOT}/ffmpeg-${FFMPEG_VERSION}"
        ./configure \
            --prefix="${FFMPEG_PREFIX}" \
            --enable-shared \
            --disable-static \
            --enable-pic \
            --disable-programs \
            --disable-doc \
            --disable-debug \
            --disable-autodetect \
            --disable-everything \
            --disable-x86asm \
            --enable-avcodec \
            --enable-avformat \
            --enable-avutil \
            --enable-demuxer=concat,mov \
            --enable-muxer=mp4 \
            --enable-protocol=file
        make -j"$(nproc)"
        make install
    )
}

build_ffmpeg
export PKG_CONFIG_PATH="${FFMPEG_PREFIX}/lib/pkgconfig:${FFMPEG_PREFIX}/lib64/pkgconfig:${PKG_CONFIG_PATH:-}"
export LIBRARY_PATH="${FFMPEG_PREFIX}/lib:${FFMPEG_PREFIX}/lib64:${LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="${FFMPEG_PREFIX}/lib:${FFMPEG_PREFIX}/lib64:${LD_LIBRARY_PATH:-}"
