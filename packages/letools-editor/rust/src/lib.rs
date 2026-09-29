//! A streaming RGB reducer with a process boundary to FFmpeg.
//!
//! No FFmpeg pointers, shared libraries, or Python frame objects cross this
//! boundary. FFmpeg decodes a single output file; Rust consumes a bounded raw
//! RGB stream and reduces all of its episodes in one pass with the GIL released.

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::io::Read;
use std::process::{Command, Stdio};

// Rows contain frame count, min RGB, max RGB, sum RGB, and squared sum RGB.
// Python normalizes to [0, 1] and formats the standard LeRobot [C, 1, 1] stats.
type Moments = (u64, [u8; 3], [u8; 3], [f64; 3], [f64; 3]);

fn reduce_rgb<R: Read>(
    reader: &mut R,
    pixels: usize,
    lengths: &[u64],
) -> Result<Vec<Moments>, String> {
    if pixels == 0 || lengths.is_empty() || lengths.contains(&0) {
        return Err("dimensions and episode lengths must be positive".into());
    }
    let size = pixels.checked_mul(3).ok_or("RGB frame size overflow")?;
    // One frame is retained irrespective of video/episode length. Per-channel
    // integer accumulation inside each frame avoids rounding millions of times.
    if size > 1024 * 1024 * 1024 {
        return Err("RGB frame exceeds the 1 GiB safety limit".into());
    }
    let mut frame = vec![0_u8; size];
    let mut result = Vec::with_capacity(lengths.len());
    for &length in lengths {
        let mut minimum = [255_u8; 3];
        let mut maximum = [0_u8; 3];
        let mut sums = [0_f64; 3];
        let mut squares = [0_f64; 3];
        for _ in 0..length {
            reader
                .read_exact(&mut frame)
                .map_err(|error| format!("decoded RGB stream ended early: {error}"))?;
            let mut frame_sums = [0_u64; 3];
            let mut frame_squares = [0_u64; 3];
            for pixel in frame.as_chunks::<3>().0 {
                for channel in 0..3 {
                    let value = pixel[channel];
                    minimum[channel] = minimum[channel].min(value);
                    maximum[channel] = maximum[channel].max(value);
                    frame_sums[channel] += u64::from(value);
                    frame_squares[channel] += u64::from(value) * u64::from(value);
                }
            }
            for channel in 0..3 {
                sums[channel] += frame_sums[channel] as f64;
                squares[channel] += frame_squares[channel] as f64;
            }
        }
        result.push((length, minimum, maximum, sums, squares));
    }
    let mut trailing = [0_u8; 1];
    if reader.read(&mut trailing).map_err(|e| e.to_string())? != 0 {
        return Err("decoded video contains more frames than episode metadata".into());
    }
    Ok(result)
}

#[pyfunction]
/// Decode one file and compute all-frame, all-pixel episode statistics.
fn video_rgb_moments(
    py: Python<'_>,
    command: Vec<String>,
    width: usize,
    height: usize,
    lengths: Vec<u64>,
) -> PyResult<Vec<Moments>> {
    py.detach(|| {
        let (program, args) = command.split_first().ok_or("missing FFmpeg command")?;
        let pixels = width.checked_mul(height).ok_or("RGB dimensions overflow")?;
        let mut child = Command::new(program)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| format!("start FFmpeg: {error}"))?;
        let mut stdout = child.stdout.take().ok_or("missing FFmpeg stdout")?;
        let mut stderr = child.stderr.take().ok_or("missing FFmpeg stderr")?;
        // Drain errors concurrently to avoid a full stderr pipe deadlocking a
        // corrupted input. Keep only the first 16 KiB of diagnostic text.
        let drain = std::thread::spawn(move || {
            let mut saved = Vec::new();
            let mut buffer = [0_u8; 4096];
            while let Ok(n) = stderr.read(&mut buffer) {
                if n == 0 {
                    break;
                }
                let keep = n.min(16384_usize.saturating_sub(saved.len()));
                saved.extend_from_slice(&buffer[..keep]);
            }
            saved
        });
        let reduced = reduce_rgb(&mut stdout, pixels, &lengths);
        drop(stdout);
        if reduced.is_err() {
            let _ = child.kill();
        }
        let status = child.wait().map_err(|error| error.to_string());
        let diagnostic = drain.join().unwrap_or_default();
        let result = reduced?;
        if !status?.success() {
            return Err(format!(
                "FFmpeg decode failed: {}",
                String::from_utf8_lossy(&diagnostic)
            ));
        }
        Ok(result)
    })
    .map_err(PyRuntimeError::new_err)
}

#[pymodule]
mod _native {
    #[pymodule_export]
    use super::video_rgb_moments;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_moments_across_episode_boundaries() {
        let bytes = [0, 10, 20, 2, 12, 22, 100, 110, 120];
        let rows = reduce_rgb(&mut &bytes[..], 1, &[2, 1]).unwrap();
        assert_eq!(
            rows[0],
            (
                2,
                [0, 10, 20],
                [2, 12, 22],
                [2., 22., 42.],
                [4., 244., 884.]
            )
        );
        assert_eq!(rows[1].0, 1);
        assert_eq!(rows[1].1, [100, 110, 120]);
    }

    #[test]
    fn reject_truncated_extra_or_empty_streams() {
        assert!(reduce_rgb(&mut &[0, 1][..], 1, &[1]).is_err());
        assert!(reduce_rgb(&mut &[0, 1, 2, 3][..], 1, &[1]).is_err());
        assert!(reduce_rgb(&mut &[][..], 0, &[1]).is_err());
        assert!(reduce_rgb(&mut &[][..], 1, &[0]).is_err());
    }
}
