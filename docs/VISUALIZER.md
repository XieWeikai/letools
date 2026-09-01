# Dataset Visualizer

`letools visualizer` runs the complete pinned Hugging Face LeRobot Dataset
Visualizer for either a local dataset path or a Hub `org/dataset` ID. The
upstream Next.js application is pinned as a Git submodule; letools prepares a
patched cache copy, supplies local services, and supervises the processes.

## Prerequisites and one-time setup

Install letools normally, then ensure Bun is available:

```bash
uv tool install .
bun --version
letools visualizer setup
```

The pinned upstream commit currently uses Bun 1.3.9. `setup` copies the app to
`${XDG_CACHE_HOME:-$HOME/.cache}/letools/visualizer/<commit>/source`, applies the
reviewed letools patches, and runs `bun install --frozen-lockfile`. The pristine
submodule checkout is never modified. Repeated setup is a fingerprinted no-op
unless the upstream commit, patch set, lockfile, or Bun version changes.

Use an existing non-`PATH` Bun or a different cache location with:

```bash
letools visualizer setup --bun /opt/bun/bin/bun --cache-dir /shared/cache
```

`--force` rebuilds the patched copy and reinstalls its locked dependencies.
LeTools does not silently install a system executable; Bun may be installed
system-wide or user-wide and needs no root privileges.

## Local datasets

Run a v2.1 or v3.x dataset directly from its directory:

```bash
letools visualizer serve /data/dataset
```

The startup JSON prints the exact page URL and all service origins. The default
ports are:

| Service | Port | Purpose |
| --- | ---: | --- |
| Next.js UI | 3000 | Browser interface and Hub API proxy |
| Local data bridge | 8765 | Metadata, Parquet, video Range, and Doctor report |
| Annotation API | 7861 | Load, edit, export, and Hub-push annotation operations |

Open the printed URL, not only the root page. `--open` opens it after the UI is
ready. The UI provides episode navigation, synchronized camera/data playback,
overview and statistics, action insights, episode filtering, interactive URDF
poses, and the language/VQA annotation editor.

The local data bridge serves only one resolved dataset root through the Hub URL
shape expected by upstream:

```text
/<stable-local-alias>/resolve/main/<dataset-relative-path>
```

It supports GET, HEAD, CORS, and single HTTP byte ranges required by browser
video playback. Traversal and symlink escapes outside the dataset root are
rejected. No dataset is copied into the application cache.

### Doctor sampling

Local reports run all checks lazily against up to 20 episodes by default:

```bash
letools visualizer serve /data/dataset --doctor-max-episodes 100
letools visualizer serve /data/dataset --doctor-max-episodes 0  # all episodes
```

Sampling affects only the embedded Doctor report, not visualizer episode access.

### Annotations

The annotation backend is enabled by default. For local v3 data, letools maps
the stable browser alias back to the real path in memory, then delegates load,
atom editing, export, and push operations to the complete upstream FastAPI
backend. The viewer works for v2.1, but persistent language-column export
requires the v3 physical layout supported by upstream.

Disable the backend when only read-only inspection is needed:

```bash
letools visualizer serve /data/dataset --no-annotations
```

Without a backend, the upstream annotation panel keeps edits in browser session
storage only. Annotation export mutates Parquet output by design; retain a copy
and validate exported data before publication.

## Hub datasets

Pass an exact repository ID:

```bash
letools visualizer serve lerobot/pusht
```

The UI fetches dataset files through its upstream Hugging Face proxy. The
annotation API accepts Hub IDs and uses `HF_TOKEN` or the normal Hugging Face
credentials available to its Python process for private repositories and push
operations. The embedded Doctor tab uses the upstream public Doctor Space for
Hub targets.

## Slurm and remote nodes

Run the service inside the allocation that can see the dataset. For a compute
node reachable only through SSH, bind the loopback defaults and forward all
enabled ports:

```bash
srun --partition=dev --cpus-per-task=4 --mem=16G \
  letools visualizer serve /data/dataset

ssh \
  -L 3000:COMPUTE_NODE:3000 \
  -L 8765:COMPUTE_NODE:8765 \
  -L 7861:COMPUTE_NODE:7861 \
  LOGIN_HOST
```

Then open the printed `http://127.0.0.1:3000/...` URL locally. If the cluster
uses a reverse proxy, bind `--host 0.0.0.0` and provide browser-reachable data
and annotation origins:

```bash
letools visualizer serve /data/dataset \
  --host 0.0.0.0 \
  --public-data-url https://data.example.org \
  --public-annotation-url https://annotations.example.org
```

The public origins must route to `--data-port` and `--annotation-port` and must
be protected appropriately. These Python services have no built-in user
authentication; do not expose them directly to an untrusted network.

## Runtime options

| Option | Meaning |
| --- | --- |
| `--host HOST` | Bind address for all services; default `127.0.0.1` |
| `--port N` | Next.js port; default `3000` |
| `--data-port N` | local dataset/Doctor bridge port; default `8765` |
| `--annotation-port N` | FastAPI annotation port; default `7861` |
| `--public-data-url URL` | browser-visible origin replacing the local data origin |
| `--public-annotation-url URL` | browser-visible origin replacing the local annotation origin |
| `--no-annotations` | do not start or advertise the annotation backend |
| `--doctor-max-episodes N` | local Doctor sample; `0` means all |
| `--production` | build and run optimized Next.js instead of development mode |
| `--open` | open the exact episode URL when ready |
| `--cache-dir PATH` | override the prepared application cache |
| `--bun PATH` | select a Bun executable explicitly |
| `--force-setup` | recreate source cache and reinstall locked dependencies |

Development mode minimizes startup time. `--production` performs `next build`
before `next start`, so it is useful for a persistent deployment rather than a
short inspection session.

## Process and dependency flow

```text
letools visualizer serve TARGET
        |
        +-- resolve local path or Hub repo ID
        +-- shallow-validate local LeRobot layout
        +-- verify patched app fingerprint
        +-- verify Bun + frozen dependency fingerprint
        |
        +-- local only: Hub-compatible file/Range server
        |                 +-- lazy Doctor HTML + JSON
        +-- optional: upstream annotation FastAPI backend
        +-- upstream Next.js application
        |
        +-- supervise until Ctrl-C, then stop every child/service
```

LeTools owns lifecycle, local routing, security confinement, and configuration.
The browser UI and annotation implementation remain upstream code. The only
source patch makes the Doctor iframe URL configurable; it is recorded under
`third_party/patches/` and applied only to the cache copy.

## Troubleshooting

`Bun is required` means `bun` was not found. Install Bun for the current user or
pass `--bun`. A failed frozen install indicates the exact lock graph could not be
resolved; check network/proxy access and rerun `setup --force`.

Port-in-use failures are fixed by selecting three free ports and forwarding the
same values. A browser that loads the UI but not local data normally cannot
reach the printed `dataset_origin`; forward the data port or supply a public
origin. Hub authorization errors require credentials in the process running
letools, not only a browser login.
