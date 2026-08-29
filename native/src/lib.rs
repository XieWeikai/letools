use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::fs;
use std::path::PathBuf;

#[cfg(feature = "video")]
use ffmpeg_next as ffmpeg;
#[cfg(feature = "video")]
use sha2::{Digest, Sha256};

#[pyfunction]
fn file_sizes(py: Python<'_>, paths: Vec<PathBuf>) -> PyResult<Vec<u64>> {
    let result: Result<Vec<_>, String> = py.detach(|| {
        paths
            .par_iter()
            .map(|path| {
                fs::metadata(path)
                    .map(|metadata| metadata.len())
                    .map_err(|error| format!("{}: {error}", path.display()))
            })
            .collect()
    });
    result.map_err(PyOSError::new_err)
}

#[pyfunction]
fn copy_files(py: Python<'_>, files: Vec<(PathBuf, PathBuf)>) -> PyResult<Vec<u64>> {
    let result: Result<Vec<_>, String> = py.detach(|| {
        files
            .par_iter()
            .map(|(source, destination)| {
                if let Some(parent) = destination.parent() {
                    fs::create_dir_all(parent)
                        .map_err(|error| format!("{}: {error}", parent.display()))?;
                }
                fs::copy(source, destination).map_err(|error| {
                    format!("{} -> {}: {error}", source.display(), destination.display())
                })
            })
            .collect()
    });
    result.map_err(PyOSError::new_err)
}

#[cfg(feature = "video")]
fn digest_packets(path: &PathBuf, slices: &[(f64, f64)]) -> Result<Vec<String>, String> {
    if slices.is_empty() {
        return Ok(Vec::new());
    }
    if slices.iter().any(|(start, end)| start > end) {
        return Err("video slice starts after it ends".to_string());
    }

    ffmpeg::init().map_err(|error| format!("initialize FFmpeg: {error}"))?;
    let mut input =
        ffmpeg::format::input(path).map_err(|error| format!("open {}: {error}", path.display()))?;
    let (video_index, time_base) = input
        .streams()
        .find(|stream| stream.parameters().medium() == ffmpeg::media::Type::Video)
        .map(|stream| (stream.index(), f64::from(stream.time_base())))
        .ok_or_else(|| format!("{} has no video stream", path.display()))?;

    let mut digests = vec![Sha256::new(); slices.len()];
    let mut slice_index = 0;
    for (stream, packet) in input.packets() {
        if stream.index() != video_index || packet.dts().is_none() {
            continue;
        }
        let timestamp = packet.pts().unwrap_or_else(|| packet.dts().unwrap()) as f64 * time_base;
        while slice_index + 1 < slices.len() && timestamp >= slices[slice_index].1 - 1e-7 {
            slice_index += 1;
        }
        let (start, end) = slices[slice_index];
        if start - 1e-7 <= timestamp
            && timestamp < end - 1e-7
            && let Some(payload) = packet.data()
        {
            digests[slice_index].update(payload);
        }
    }
    Ok(digests
        .into_iter()
        .map(|digest| format!("{:x}", digest.finalize()))
        .collect())
}

#[cfg(feature = "video")]
#[pyfunction]
fn packet_digests(py: Python<'_>, path: PathBuf, slices: Vec<(f64, f64)>) -> PyResult<Vec<String>> {
    py.detach(|| digest_packets(&path, &slices))
        .map_err(PyOSError::new_err)
}

#[pyfunction]
fn build_info() -> (&'static str, Vec<&'static str>) {
    let capabilities = vec!["filesystem"];
    #[cfg(feature = "video")]
    let capabilities = {
        let mut capabilities = capabilities;
        capabilities.push("video-packet-digests");
        capabilities
    };
    (env!("CARGO_PKG_VERSION"), capabilities)
}

#[pymodule]
mod letools_native {
    #[pymodule_export]
    use super::{build_info, copy_files, file_sizes};

    #[cfg(feature = "video")]
    #[pymodule_export]
    use super::packet_digests;
}
