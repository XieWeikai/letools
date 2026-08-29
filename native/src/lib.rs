use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::fs;
use std::path::{Path, PathBuf};

#[cfg(feature = "video")]
use ffmpeg_next as ffmpeg;
#[cfg(feature = "video")]
use sha2::{Digest, Sha256};
#[cfg(feature = "video")]
use std::io::Write;

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
fn concat_videos(inputs: &[PathBuf], output: &PathBuf) -> Result<(), String> {
    if inputs.is_empty() {
        return Err("at least one input video is required".to_string());
    }
    ffmpeg::init().map_err(|error| format!("initialize FFmpeg: {error}"))?;

    let mut listing = tempfile::Builder::new()
        .suffix(".ffconcat")
        .tempfile()
        .map_err(|error| format!("create concat list: {error}"))?;
    writeln!(listing, "ffconcat version 1.0")
        .map_err(|error| format!("write concat list: {error}"))?;
    for path in inputs {
        let resolved = path
            .canonicalize()
            .map_err(|error| format!("{}: {error}", path.display()))?;
        let escaped = resolved.to_string_lossy().replace('\'', "'\\''");
        writeln!(listing, "file '{escaped}'")
            .map_err(|error| format!("write concat list: {error}"))?;
    }
    listing
        .flush()
        .map_err(|error| format!("flush concat list: {error}"))?;

    let mut options = ffmpeg::Dictionary::new();
    options.set("safe", "0");
    let mut input = ffmpeg::format::input_with_dictionary(listing.path(), options)
        .map_err(|error| format!("open concat input: {error}"))?;
    let parent = output
        .parent()
        .ok_or_else(|| format!("{} has no parent directory", output.display()))?;
    fs::create_dir_all(parent).map_err(|error| format!("{}: {error}", parent.display()))?;
    let temporary = tempfile::Builder::new()
        .suffix(".mp4")
        .tempfile_in(parent)
        .map_err(|error| format!("create temporary output: {error}"))?
        .into_temp_path();
    let mut destination = ffmpeg::format::output(&temporary)
        .map_err(|error| format!("open temporary output: {error}"))?;

    let mut stream_mapping = vec![-1_isize; input.nb_streams() as usize];
    let mut input_time_bases = vec![ffmpeg::Rational(0, 1); input.nb_streams() as usize];
    let mut output_index = 0_isize;
    for (input_index, stream) in input.streams().enumerate() {
        let medium = stream.parameters().medium();
        if !matches!(
            medium,
            ffmpeg::media::Type::Audio | ffmpeg::media::Type::Video | ffmpeg::media::Type::Subtitle
        ) {
            continue;
        }
        stream_mapping[input_index] = output_index;
        input_time_bases[input_index] = stream.time_base();
        output_index += 1;
        let mut target = destination
            .add_stream(ffmpeg::encoder::find(ffmpeg::codec::Id::None))
            .map_err(|error| format!("add output stream: {error}"))?;
        target.set_parameters(stream.parameters());
        target.set_time_base(stream.time_base());
        unsafe {
            (*target.parameters().as_mut_ptr()).codec_tag = 0;
        }
    }

    destination.set_metadata(input.metadata().to_owned());
    destination
        .write_header()
        .map_err(|error| format!("write output header: {error}"))?;
    for (stream, mut packet) in input.packets() {
        let input_index = stream.index();
        let mapped_index = stream_mapping[input_index];
        if mapped_index < 0 || packet.dts().is_none() {
            continue;
        }
        let output_time_base = destination
            .stream(mapped_index as usize)
            .ok_or_else(|| "missing output stream".to_string())?
            .time_base();
        packet.rescale_ts(input_time_bases[input_index], output_time_base);
        packet.set_stream(mapped_index as usize);
        packet
            .write_interleaved(&mut destination)
            .map_err(|error| format!("write packet: {error}"))?;
    }
    destination
        .write_trailer()
        .map_err(|error| format!("write output trailer: {error}"))?;

    drop(destination);
    temporary
        .persist(output)
        .map_err(|error| format!("publish {}: {error}", output.display()))?;
    Ok(())
}

#[cfg(feature = "video")]
struct SplitOutput {
    context: ffmpeg::format::context::Output,
    temporary: tempfile::TempPath,
    target: PathBuf,
    stream_mapping: Vec<isize>,
    output_time_bases: Vec<ffmpeg::Rational>,
    timestamp_offsets: Vec<i64>,
}

#[cfg(feature = "video")]
fn open_split_output(
    target: &Path,
    start: f64,
    stream_count: usize,
    streams: &[(usize, ffmpeg::codec::Parameters, ffmpeg::Rational)],
) -> Result<SplitOutput, String> {
    let parent = target
        .parent()
        .ok_or_else(|| format!("{} has no parent directory", target.display()))?;
    fs::create_dir_all(parent).map_err(|error| format!("{}: {error}", parent.display()))?;
    let temporary = tempfile::Builder::new()
        .suffix(".mp4")
        .tempfile_in(parent)
        .map_err(|error| format!("create temporary output: {error}"))?
        .into_temp_path();
    let mut context = ffmpeg::format::output(&temporary)
        .map_err(|error| format!("open temporary output: {error}"))?;
    let mut stream_mapping = vec![-1_isize; stream_count];
    let mut timestamp_offsets = vec![0_i64; stream_count];
    for (output_index, (input_index, parameters, time_base)) in streams.iter().enumerate() {
        stream_mapping[*input_index] = output_index as isize;
        timestamp_offsets[*input_index] = (start / f64::from(*time_base)).round() as i64;
        let mut output_stream = context
            .add_stream(ffmpeg::encoder::find(ffmpeg::codec::Id::None))
            .map_err(|error| format!("add output stream: {error}"))?;
        output_stream.set_parameters(parameters.clone());
        output_stream.set_time_base(*time_base);
        unsafe {
            (*output_stream.parameters().as_mut_ptr()).codec_tag = 0;
        }
    }
    context
        .write_header()
        .map_err(|error| format!("write output header: {error}"))?;
    let output_time_bases = context.streams().map(|stream| stream.time_base()).collect();
    Ok(SplitOutput {
        context,
        temporary,
        target: target.to_path_buf(),
        stream_mapping,
        output_time_bases,
        timestamp_offsets,
    })
}

#[cfg(feature = "video")]
fn close_split_output(mut output: SplitOutput) -> Result<(), String> {
    output
        .context
        .write_trailer()
        .map_err(|error| format!("write {} trailer: {error}", output.target.display()))?;
    drop(output.context);
    output
        .temporary
        .persist(&output.target)
        .map_err(|error| format!("publish {}: {error}", output.target.display()))?;
    Ok(())
}

#[cfg(feature = "video")]
fn split_video_slices(source: &PathBuf, outputs: &[(f64, f64, PathBuf)]) -> Result<(), String> {
    if outputs.is_empty() {
        return Ok(());
    }
    if outputs
        .iter()
        .enumerate()
        .any(|(index, (start, end, _))| start > end || (index > 0 && *start < outputs[index - 1].0))
    {
        return Err("video slices must be valid and ordered by start time".to_string());
    }

    ffmpeg::init().map_err(|error| format!("initialize FFmpeg: {error}"))?;
    let mut input = ffmpeg::format::input(source)
        .map_err(|error| format!("open {}: {error}", source.display()))?;
    let stream_count = input.nb_streams() as usize;
    let streams: Vec<_> = input
        .streams()
        .filter(|stream| {
            matches!(
                stream.parameters().medium(),
                ffmpeg::media::Type::Audio
                    | ffmpeg::media::Type::Video
                    | ffmpeg::media::Type::Subtitle
            )
        })
        .map(|stream| {
            (
                stream.index(),
                stream.parameters().clone(),
                stream.time_base(),
            )
        })
        .collect();
    if streams.is_empty() {
        return Err(format!("{} has no supported streams", source.display()));
    }
    let input_time_bases: Vec<_> = input.streams().map(|stream| stream.time_base()).collect();

    let mut current_index: Option<usize> = None;
    let mut active: Option<SplitOutput> = None;
    for (stream, mut packet) in input.packets() {
        let input_index = stream.index();
        if packet.dts().is_none()
            || !matches!(
                stream.parameters().medium(),
                ffmpeg::media::Type::Audio
                    | ffmpeg::media::Type::Video
                    | ffmpeg::media::Type::Subtitle
            )
        {
            continue;
        }
        let timestamp_value = packet.pts().unwrap_or_else(|| packet.dts().unwrap());
        let timestamp = timestamp_value as f64 * f64::from(input_time_bases[input_index]);
        while current_index.map_or(0, |index| index + 1) < outputs.len()
            && timestamp >= outputs[current_index.map_or(0, |index| index + 1)].0 - 1e-7
        {
            if let Some(output) = active.take() {
                close_split_output(output)?;
            }
            let next_index = current_index.map_or(0, |index| index + 1);
            active = Some(open_split_output(
                &outputs[next_index].2,
                outputs[next_index].0,
                stream_count,
                &streams,
            )?);
            current_index = Some(next_index);
        }

        let Some(index) = current_index else {
            continue;
        };
        let Some(output) = active.as_mut() else {
            continue;
        };
        if timestamp >= outputs[index].1 - 1e-7 {
            continue;
        }
        let mapped_index = output.stream_mapping[input_index];
        if mapped_index < 0 {
            continue;
        }
        if let Some(pts) = packet.pts() {
            packet.set_pts(Some(pts - output.timestamp_offsets[input_index]));
        }
        packet.set_dts(
            packet
                .dts()
                .map(|dts| dts - output.timestamp_offsets[input_index]),
        );
        packet.rescale_ts(
            input_time_bases[input_index],
            output.output_time_bases[mapped_index as usize],
        );
        packet.set_stream(mapped_index as usize);
        packet
            .write_interleaved(&mut output.context)
            .map_err(|error| format!("write {} packet: {error}", output.target.display()))?;
    }
    if let Some(output) = active {
        close_split_output(output)?;
    }
    Ok(())
}

#[cfg(feature = "video")]
#[pyfunction]
fn packet_digests(py: Python<'_>, path: PathBuf, slices: Vec<(f64, f64)>) -> PyResult<Vec<String>> {
    py.detach(|| digest_packets(&path, &slices))
        .map_err(PyOSError::new_err)
}

#[cfg(feature = "video")]
#[pyfunction]
fn concatenate_videos(py: Python<'_>, inputs: Vec<PathBuf>, output: PathBuf) -> PyResult<()> {
    py.detach(|| concat_videos(&inputs, &output))
        .map_err(PyOSError::new_err)
}

#[cfg(feature = "video")]
#[pyfunction]
fn split_video(py: Python<'_>, source: PathBuf, outputs: Vec<(f64, f64, PathBuf)>) -> PyResult<()> {
    py.detach(|| split_video_slices(&source, &outputs))
        .map_err(PyOSError::new_err)
}

#[pyfunction]
fn build_info() -> (&'static str, Vec<&'static str>) {
    let capabilities = vec!["filesystem"];
    #[cfg(feature = "video")]
    let capabilities = {
        let mut capabilities = capabilities;
        capabilities.push("video-packet-digests");
        capabilities.push("video-concat");
        capabilities.push("video-split");
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
    use super::{concatenate_videos, packet_digests, split_video};
}
