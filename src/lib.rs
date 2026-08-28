use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::fs;
use std::path::PathBuf;

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

/// A Python module implemented in Rust. The name of this module must match
/// the `lib.name` setting in the `Cargo.toml`, else Python will not be able to
/// import the module.
#[pymodule]
mod _core {
    #[pymodule_export]
    use super::{copy_files, file_sizes};
}
