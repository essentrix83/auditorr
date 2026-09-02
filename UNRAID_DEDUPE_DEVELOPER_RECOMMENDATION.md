# Unraid Dedupe: Accurate Classification, Not Automated Migration

## Summary

Auditorr should distinguish *duplicate detection* from *safe, immediately
actionable deduplication* on Unraid.

An Unraid user share such as `/mnt/user/data` is a logical view over several
physical array disks. Arr can successfully create a hardlink from a torrent
path to a library path through that one user share: SHFS places the new link on
the source file's backing disk. This is already working on this installation.

However, Dedupe can discover two pre-existing, byte-identical copies on
different physical disks. Those copies cannot be hardlinked together in place.
They are not failed hardlinks and must not be automatically moved, deleted, or
reported as reclaimable now.

## Observed installation facts

- qBittorrent uses `/mnt/user/data/torrents` and the Arr containers use
  `/mnt/user/data`, all with the common in-container `/data` namespace.
- The `data` share spans array disks 3, 5, and 6.
- The live Auditorr r2 deployment mounts `/data` read-only and has read-only
  backing-root views for those disks.
- The audit reports roughly 37 TB of already hardlinked library media. This is
  evidence that normal Arr imports through the user share are working.
- The r2 Dedupe patch resolves logical paths to backing roots, fails closed on
  ambiguous paths, and stages a temporary hardlink before atomically replacing
  a same-filesystem duplicate.

## Problem to solve

The Dedupe UI and API need to make physical feasibility and expected space
reclamation unambiguous. A cross-device content match is useful investigative
information, but it is not a hardlink operation that can be performed now.

Without explicit states, users can mistake a duplicate report for a request to
move files or delete one copy. That would be unsafe in a media stack where
Plex, Arr, and qBittorrent each have independent ownership and state.

## Requested behaviour

Classify each candidate group using real backing-filesystem evidence:

| State | Required evidence | UI/action behaviour |
| --- | --- | --- |
| `already_hardlinked` | Same backing device and inode; link count at least 2 | Informational; no dedupe action and zero additional reclaimable bytes. |
| `reclaimable_now` | Same backing device, different inode, same size, exact byte comparison succeeds | May generate a *staged*, reviewed hardlink plan. Count bytes as reclaimable only after all preconditions pass. |
| `cross_device` | Content candidate resolves to different backing devices | Report-only; zero reclaimable-now bytes; never auto-move or auto-delete. |
| `blocked` | Missing, ambiguous, inaccessible, stale, or incomplete backing/content evidence | Report-only with a specific blocker; no generated action. |

## Unraid implementation notes

1. Treat the `/data` user-share device number as non-authoritative for hardlink
   eligibility. It is an SHFS/FUSE view, not the backing filesystem identity.
2. Support an optional, explicitly configured backing-root resolver. For a
   logical `/data/path`, inspect the equivalent path in each read-only backing
   root and accept it only when it exists in exactly one root.
3. Compare physical `st_dev`, inode, size, and link count using that resolved
   path. If either path cannot be uniquely resolved, use `blocked`.
4. Use a full byte comparison before proposing a link. A fast or partial hash
   may identify candidates but must never authorize linking.
5. Create a temporary sibling hardlink first, then atomically replace the
   duplicate. Do not use `ln -f source destination`; cross-device failure can
   be destructive after destination replacement is attempted.
6. Retain paths under one common user share in Docker. Do not mix `/mnt/user`
   and `/mnt/diskN` paths in one operation.

## Explicit non-goals

- Do not automatically move a cross-device duplicate to another array disk.
- Do not delete a duplicate merely because it is cross-device, unregistered,
  superseded, or absent from qBittorrent.
- Do not make Auditorr a Plex deletion authority.
- Do not infer Plex/Arr/qBittorrent ownership from a filename, size, title, or
  quality comparison alone.

Cross-device consolidation is a separately approved storage migration. It
requires complete qBittorrent group evidence, Arr/Plex provenance, a defined
destination, and post-move validation; it is outside Dedupe.

## Acceptance criteria

1. A same-device, exact-match group is shown as `reclaimable_now` and its
   generated plan verifies bytes, stages the link, and preserves every path.
2. A cross-device, exact-match group is shown as `cross_device`, contributes
   zero to reclaimable-now totals, and cannot generate a link plan.
3. A group with ambiguous or missing backing-root resolution is `blocked` and
   cannot generate a link plan.
4. Existing hardlink aliases are grouped once by physical file identity, not
   once per reciprocal path.
5. A group already sharing an inode is `already_hardlinked` and never inflates
   duplicate or reclaimable-byte counts.
6. Tests cover SHFS logical paths backed by different disks, same-disk copies,
   reciprocal aliases, inaccessible paths, and exact-comparison failure.

## Value

This change makes Auditorr's storage figures trustworthy:

- **Reclaimable now** means a safe same-disk hardlink opportunity.
- **Cross-device** means a related copy that needs no automatic remediation.
- **Already hardlinked** prevents duplicate reporting and duplicate actions.
- **Blocked** turns uncertainty into an explicit review requirement.

It improves user trust and avoids unsafe actions without requiring Auditorr to
take ownership of Plex, Arr, qBittorrent, or disk migrations.
