#!/bin/bash
# Run from a source checkout with the optional editor installed. Override the
# node/partition/output path with sbatch flags to match the local cluster.
#SBATCH --job-name=letools-editor-test
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=00:30:00
set -euo pipefail
export PYTHONPATH="$PWD/tests${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export RAYON_NUM_THREADS=16
export TMPDIR="${EDITOR_TEST_TMPDIR:-${SLURM_TMPDIR:-${TMPDIR:-/tmp}}/letools-editor-tests}"
mkdir -p "$TMPDIR"
.venv/bin/python -m pytest -q packages/letools-editor/tests tests
