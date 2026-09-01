# External source patches

Patch files in this directory are applied to a user-cache copy of an immutable
upstream snapshot. They must apply with zero fuzz. Keeping the upstream source
unchanged makes provenance and future upgrades reviewable.

Patch ordering is lexical within each project directory. Every patch must state
why the integration cannot be implemented entirely in the letools adapter.

