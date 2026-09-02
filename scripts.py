import os
import posixpath
import shlex
import logging
from datetime import datetime

log = logging.getLogger(__name__)


def _mount_fstype(path):
    """Return the filesystem type for the most-specific Linux mount at path."""
    path = os.path.abspath(path)
    best = ('', None)
    try:
        with open('/proc/self/mountinfo', encoding='utf-8') as handle:
            for line in handle:
                try:
                    left, right = line.rstrip('\n').split(' - ', 1)
                    mount_path = left.split()[4].replace('\\040', ' ')
                    fs_type = right.split()[0]
                except (IndexError, ValueError):
                    continue
                mount_path = mount_path.rstrip('/') or '/'
                if (path == mount_path or path.startswith(mount_path.rstrip('/') + '/')) \
                        and len(mount_path) > len(best[0]):
                    best = (mount_path, fs_type)
    except OSError:
        pass
    return best[1]


def _backing_roots():
    """Configured read-only views of the physical filesystems behind /data."""
    raw = os.environ.get('AUDITORR_BACKING_ROOTS', '')
    return [os.path.abspath(root) for root in raw.split(os.pathsep) if root]


def _backing_stat(path):
    """Return a physical stat result only when one backing root owns ``path``.

    Unraid's SHFS mount has a synthetic device ID.  A duplicate candidate is
    deliberately blocked when the logical path maps to no backing root or more
    than one; either condition makes a hardlink decision unsafe.
    """
    roots = _backing_roots()
    if not roots:
        return None
    source_root = os.path.abspath(
        os.environ.get('AUDITORR_BACKING_SOURCE_ROOT', '/data'))
    path = os.path.abspath(path)
    try:
        if os.path.commonpath((source_root, path)) != source_root:
            return None
    except ValueError:
        return None
    relative = os.path.relpath(path, source_root)
    matches = [os.path.join(root, relative) for root in roots
               if os.path.exists(os.path.join(root, relative))]
    if len(matches) != 1:
        log.warning('Cannot resolve one physical backing path for %s (%d matches)',
                    path, len(matches))
        return None
    try:
        return os.stat(matches[0])
    except OSError as exc:
        log.warning('Cannot stat physical backing path for %s: %s', path, exc)
        return None


def _physical_stat(path):
    """Stat a real filesystem; never approve a hardlink from SHFS identity."""
    backing = _backing_stat(path)
    if backing is not None:
        return backing
    if _mount_fstype(path) == 'fuse.shfs':
        log.warning('Refusing to infer hardlink compatibility through Unraid SHFS: %s', path)
        return None
    try:
        return os.stat(path)
    except OSError:
        return None


def _human_size(n):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def _compute_script_root(local_path, media_path):
    """Return the directory all script paths should be relative to."""
    if not media_path or local_path == media_path:
        return local_path
    try:
        common = posixpath.commonpath([local_path, media_path])
    except ValueError:
        return local_path
    if common in ('', '/'):
        return local_path
    return common


def dup_group_inputs(torrent_files, media_files, local_path, media_path):
    """Tag files with their filesystem root for _build_dup_groups, keeping only
    the files group building can actually use: excluded ones (they feed the
    partner filter) and ones with duplicate partners. Copying every record just
    to add the tag doubled the multi-GB parsed lists on very large libraries."""
    def keep(f):
        return f.get('excluded') or f.get('duplicate_paths')
    return ([{**f, '_file_root': local_path} for f in torrent_files if keep(f)]
            + [{**f, '_file_root': media_path} for f in media_files if keep(f)])


def _build_dup_groups(all_files, local_path, media_path=''):
    """Group files with duplicate_paths into structured groups for the Actions page.

    Files marked excluded never appear in a group — not as the canonical copy and
    not as a duplicate partner — so generated scripts never touch them (#14).
    """
    script_root   = _compute_script_root(local_path, media_path)
    groups        = []
    seen_file_ids = set()
    covered_file_ids = set()
    covered_paths = set()  # absolute paths already assigned to any group slot

    # Map every known hardlink alias to its physical file identity.  This keeps
    # reciprocal aliases from becoming multiple Dedupe groups.
    path_to_file_id = {}
    for f in all_files:
        file_root = f.get('_file_root', local_path)
        file_id = f.get('file_id', f.get('inode'))
        if not file_id:
            continue
        canonical = posixpath.join(file_root, f['path']) if file_root else f['path']
        path_to_file_id[canonical] = file_id
        for linked_path in f.get('linked_paths') or []:
            path_to_file_id[linked_path] = file_id

    # Absolute paths of every excluded file, so excluded *partners* can be
    # dropped from other files' duplicate lists (duplicate_paths entries are
    # absolute paths with no excluded flag of their own).
    excluded_abs   = set()
    excluded_count = 0
    for f in all_files:
        if f.get('excluded'):
            file_root = f.get('_file_root', local_path)
            excluded_abs.add(posixpath.join(file_root, f['path']) if file_root else f['path'])
            if f.get('duplicate_paths'):
                excluded_count += 1

    for f in all_files:
        if not f.get('duplicate_paths') or f.get('excluded'):
            continue
        inode   = f['inode']
        file_id = f.get('file_id', inode)
        if file_id in seen_file_ids or file_id in covered_file_ids:
            continue
        file_root  = f.get('_file_root', local_path)
        canon_full = posixpath.join(file_root, f['path']) if file_root else f['path']
        if canon_full in covered_paths:
            continue
        dup_paths = []
        for duplicate_path in f.get('duplicate_paths', []):
            if duplicate_path in excluded_abs:
                continue
            partner_id = path_to_file_id.get(duplicate_path)
            if partner_id is not None and partner_id in covered_file_ids:
                continue
            dup_paths.append(duplicate_path)
        if not dup_paths:
            continue  # every partner is excluded — nothing left to dedupe
        seen_file_ids.add(file_id)
        covered_file_ids.add(file_id)
        covered_paths.add(canon_full)

        canon_rel = posixpath.relpath(canon_full, script_root)
        canon_stat = _physical_stat(canon_full)
        group_files = [{"path": canon_rel, "size": f['size'], "inode": inode, "canonical": True, "same_fs": True}]
        status = 'reclaimable_now'
        blocker = ''
        for dup_path in dup_paths:
            covered_paths.add(dup_path)
            partner_id = path_to_file_id.get(dup_path)
            if partner_id is not None:
                covered_file_ids.add(partner_id)
            duplicate_stat = _physical_stat(dup_path)
            if canon_stat is None or duplicate_stat is None:
                same_fs = False
                status = 'blocked'
                blocker = 'physical backing location could not be uniquely verified'
            else:
                same_fs = duplicate_stat.st_dev == canon_stat.st_dev
                if not same_fs and status != 'blocked':
                    status = 'cross_device'
                    blocker = 'copies are on different physical filesystems'
            dup_rel = posixpath.relpath(dup_path, script_root)
            group_files.append({"path": dup_rel, "size": f['size'], "inode": 0, "canonical": False, "same_fs": same_fs})
        recoverable = f['size'] * len(dup_paths) if status == 'reclaimable_now' else 0
        groups.append({"files": group_files, "recoverable_size": recoverable,
                       "status": status, "blocker": blocker,
                       "skipped": status != 'reclaimable_now'})
    return {"groups": groups, "script_root": script_root, "excluded_count": excluded_count}


def generate_script(script_type, results, cfg, selection=None):
    """Generate and return a shell script string. Raises ValueError for unknown script_type.

    selection (optional dict) narrows the script to a user-chosen subset:
      {'paths': [...]}  — relative torrent paths (delete scripts)
      {'groups': [...]} — canonical relative paths of duplicate groups (dedupe)
    """
    torrent_files = results.get('torrent_files', [])
    local_path    = cfg.get('LOCAL_PATH', '')
    media_path    = cfg.get('MEDIA_PATH', '')
    now_str       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    selection     = selection or {}

    if script_type == 'orphaned_torrents_delete':
        # Excluded files are invisible to script generation — never emit a
        # delete command for something the user explicitly excluded (#14).
        orphaned       = [f for f in torrent_files if f.get('status') == 'Orphaned' and not f.get('excluded')]
        excluded_count = sum(1 for f in torrent_files if f.get('status') == 'Orphaned' and f.get('excluded'))
        if selection.get('paths'):
            wanted   = set(selection['paths'])
            orphaned = [f for f in orphaned if f['path'] in wanted]
        _validate_delete_targets(orphaned, local_path)
        return _build_delete_script(
            orphaned, excluded_count, now_str,
            title='Orphaned Torrent Cleanup Script',
            heading='auditorr Orphaned Torrent Cleanup',
            script_name='orphaned_torrents_delete.sh',
            excluded_noun='orphaned file(s)',
        )

    elif script_type == 'delete_selected':
        # Explicit selection from a workflow page (Triage / Cleanup) — only the
        # given relative paths, and excluded files still never emitted.
        wanted         = set(selection.get('paths') or [])
        if not wanted:
            raise ValueError("delete_selected requires a non-empty 'paths' selection")
        files          = [f for f in torrent_files if f['path'] in wanted and not f.get('excluded')]
        excluded_count = sum(1 for f in torrent_files if f['path'] in wanted and f.get('excluded'))
        _validate_delete_targets(files, local_path)
        return _build_delete_script(
            files, excluded_count, now_str,
            title='Selected File Cleanup Script',
            heading='auditorr Selected File Cleanup',
            script_name='delete_selected.sh',
        )

    elif script_type == 'dedupe':
        return _build_dedupe_script(results, cfg, now_str, selection)

    else:
        raise ValueError("Unknown script type")


def _delete_target_path(local_path, rel_path):
    """Resolve a stored torrent path without allowing it to escape LOCAL_PATH."""
    if not local_path or not rel_path or os.path.isabs(str(rel_path)):
        return None
    root = os.path.abspath(local_path)
    target = os.path.abspath(os.path.join(root, str(rel_path).replace('\\', os.sep)))
    try:
        if os.path.commonpath((root, target)) != root:
            return None
    except ValueError:
        return None
    return target


def _validate_delete_targets(files, local_path):
    """Reject stale audit rows before generating a destructive script."""
    stale = []
    for f in files:
        path = f.get('path', '')
        target = _delete_target_path(local_path, path)
        if not target or not os.path.isfile(target):
            stale.append(path or '<missing path>')
    if stale:
        preview = ', '.join(stale[:5])
        suffix = f' (+{len(stale) - 5} more)' if len(stale) > 5 else ''
        raise ValueError(
            'Cleanup targets changed since the audit; no script was generated. '
            f'Re-run the audit. Missing: {preview}{suffix}'
        )


def _build_delete_script(files, excluded_count, now_str, title, heading, script_name, excluded_noun='file(s)'):
    """Shared body for all delete scripts: link-count-aware rm with space accounting."""
    total_size = sum(f['size'] for f in files)
    lines = [
        '#!/bin/bash',
        f'# auditorr — {title}',
        f'# Generated: {now_str}',
        '# WARNING: Review carefully before running. This permanently deletes files.',
        f'# {len(files)} files — {_human_size(total_size)} expected to be freed',
    ]
    if excluded_count:
        lines.append(f'# {excluded_count} {excluded_noun} skipped — they match your Excluded Files & Folders settings')
    lines += [
        '#',
        '# This script will:',
        '#   1. Record free disk space before deletions',
        '#   2. Check each file\'s inode link count (hardlinked = still referenced elsewhere)',
        '#   3. Delete each file with progress output',
        '#   4. Record free disk space after deletions',
        '#   5. Compare actual space freed vs standalone-only expected',
        '#',
        '# USAGE:',
        '#   cd /path/to/your/torrent/directory',
        f'#   bash {script_name}',
        '#',
        '# All file paths are relative to your torrent directory.',
        '# Run this from wherever that directory is mounted with write access.',
        '',
        '# Format byte count for display',
        '_fmt_bytes() {',
        '  local b=$1',
        '  if [ "$b" -ge 1073741824 ]; then',
        '    echo "$((b/1073741824))GB"',
        '  elif [ "$b" -ge 1048576 ]; then',
        '    echo "$((b/1048576))MB"',
        '  elif [ "$b" -ge 1024 ]; then',
        '    echo "$((b/1024))KB"',
        '  else',
        '    echo "${b}B"',
        '  fi',
        '}',
        '',
    ]

    # Working-directory guard: validate every target before any deletion.
    if files:
        targets = [shlex.quote(f['path']) for f in files]
        lines += [
            'TARGETS=(',
            *[f'  {target}' for target in targets],
            ')',
            'for TARGET in "${TARGETS[@]}"; do',
            '  if [ ! -f "$TARGET" ]; then',
            '    echo "ERROR: Cleanup targets changed since this script was generated."',
            '    echo "  Missing: $TARGET"',
            '    echo "  Re-run the Auditorr audit and regenerate the script."',
            '    exit 1',
            '  fi',
            'done',
            '',
        ]

    lines += [
        f'TOTAL={len(files)}',
        'DONE=0',
        'ERRORS=0',
        'HARDLINKED_COUNT=0',
        'HARDLINKED_BYTES=0',
        'STANDALONE_COUNT=0',
        'STANDALONE_BYTES=0',
        '',
        '# Get free space in bytes on the relevant filesystem',
        'FREE_BEFORE=$(df --output=avail -B1 "." 2>/dev/null | tail -1 | tr -d " ")',
        '[ -z "$FREE_BEFORE" ] && FREE_BEFORE=$(df -k . 2>/dev/null | awk \'NR==2{print $4*1024}\')',
        '',
        'echo "================================================"',
        f'echo "{heading}"',
        f'echo "Files to delete: {len(files)}"',
        f'echo "Expected to free: {_human_size(total_size)}"',
        'echo "================================================"',
        'echo ""',
    ]

    for i, f in enumerate(files):
        rel_path   = f['path']  # already relative to LOCAL_PATH
        filename   = os.path.basename(rel_path)
        qfull      = shlex.quote(rel_path)
        qname      = shlex.quote(filename)
        size_bytes = f['size']
        lines += [
            f'# File {i+1}/{len(files)}: {filename} — {_human_size(size_bytes)}',
            f'printf "[{i+1}/{len(files)}] Deleting: %s ({_human_size(size_bytes)})\\n" {qname}',
            f'if [ -f {qfull} ]; then',
            f'  NLINKS=$(stat -c \'%h\' {qfull} 2>/dev/null || stat -f \'%l\' {qfull} 2>/dev/null || echo 1)',
            '  if [ "$NLINKS" -gt 1 ]; then',
            f'    printf "  (hardlinked — %s references, space freed when last link removed)\\n" "$NLINKS"',
            f'    HARDLINKED_COUNT=$((HARDLINKED_COUNT+1))',
            f'    HARDLINKED_BYTES=$((HARDLINKED_BYTES+{size_bytes}))',
            '  else',
            f'    STANDALONE_COUNT=$((STANDALONE_COUNT+1))',
            f'    STANDALONE_BYTES=$((STANDALONE_BYTES+{size_bytes}))',
            '  fi',
            f'  rm {qfull}',
            f'  echo "  ✓ Deleted"',
            '  DONE=$((DONE+1))',
            'else',
            f'  printf "  ⚠ Not found, skipping: %s\\n" {qfull}',
            '  ERRORS=$((ERRORS+1))',
            'fi',
            '',
        ]

    lines += [
        'echo ""',
        'echo "================================================"',
        'echo "Cleanup complete."',
        'echo "  Deleted:  $DONE / $TOTAL files"',
        'if [ "$HARDLINKED_COUNT" -gt 0 ]; then',
        '  HL_DISPLAY=$(_fmt_bytes "$HARDLINKED_BYTES")',
        '  echo "    Hardlinked (space not freed yet): $HARDLINKED_COUNT file(s) ($HL_DISPLAY)"',
        'fi',
        'if [ "$STANDALONE_COUNT" -gt 0 ]; then',
        '  SL_DISPLAY=$(_fmt_bytes "$STANDALONE_BYTES")',
        '  echo "    Standalone (space freed):         $STANDALONE_COUNT file(s) ($SL_DISPLAY)"',
        'fi',
        'if [ "$ERRORS" -gt 0 ]; then',
        '  echo "  Warnings: $ERRORS file(s) not found (already deleted?)"',
        'fi',
        '',
        '# Measure actual space freed',
        'FREE_AFTER=$(df --output=avail -B1 "." 2>/dev/null | tail -1 | tr -d " ")',
        '[ -z "$FREE_AFTER" ] && FREE_AFTER=$(df -k . 2>/dev/null | awk \'NR==2{print $4*1024}\')',
        '',
        f'echo "  Expected: {_human_size(total_size)} total"',
        'if [ "$HARDLINKED_COUNT" -gt 0 ] && [ "$STANDALONE_COUNT" -gt 0 ]; then',
        '  SL_DISPLAY=$(_fmt_bytes "$STANDALONE_BYTES")',
        '  echo "    ($SL_DISPLAY from standalone, $HARDLINKED_COUNT hardlinked file(s) free 0)"',
        'elif [ "$HARDLINKED_COUNT" -gt 0 ]; then',
        '  echo "    (all files were hardlinked — 0 expected to free)"',
        'fi',
        '',
        'if [ -z "$FREE_BEFORE" ] || [ -z "$FREE_AFTER" ]; then',
        '  echo "  Actual:   (unable to measure — df unavailable on this system)"',
        'else',
        '  FREED=$((FREE_AFTER - FREE_BEFORE))',
        '  FREED_DISPLAY=$(_fmt_bytes "$FREED")',
        '  echo "  Actual:   $FREED_DISPLAY"',
        '  if [ "$STANDALONE_BYTES" -gt 0 ]; then',
        '    VARIANCE=$(( (FREED - STANDALONE_BYTES) * 100 / STANDALONE_BYTES ))',
        '    ABS_VARIANCE="${VARIANCE#-}"',
        '    if [ "$ABS_VARIANCE" -le 2 ]; then',
        '      echo "  ✓ Actual matches standalone expected (within 2%)"',
        '    else',
        '      echo "  ⚠ Actual differs from standalone expected by ${VARIANCE}% — unexpected"',
        '    fi',
        '  elif [ "$HARDLINKED_COUNT" -gt 0 ] && [ "$STANDALONE_COUNT" -eq 0 ]; then',
        '    if [ "$FREED" -eq 0 ]; then',
        '      echo "  ✓ All files were hardlinked — 0 freed is correct"',
        '    else',
        '      echo "  ⚠ Files were hardlinked but disk space changed — check for concurrent activity"',
        '    fi',
        '  fi',
        'fi',
        'echo "================================================"',
    ]
    return '\n'.join(lines)


def _build_dedupe_script(results, cfg, now_str, selection):
    torrent_files  = results.get('torrent_files', [])
    media_files    = results.get('media_files', [])
    local_path     = cfg.get('LOCAL_PATH', '')
    media_path     = cfg.get('MEDIA_PATH', '')
    dup_result         = _build_dup_groups(
        dup_group_inputs(torrent_files, media_files, local_path, media_path),
        local_path, media_path)
    groups             = dup_result['groups']
    script_root        = dup_result['script_root']
    excluded_count     = dup_result.get('excluded_count', 0)
    if selection.get('groups'):
        # Group identity = canonical file's relative path (stable per audit)
        wanted = set(selection['groups'])
        groups = [g for g in groups
                  if next(f['path'] for f in g['files'] if f['canonical']) in wanted]
    total_recoverable  = sum(g['recoverable_size'] for g in groups)
    skipped_count      = sum(1 for g in groups if g['skipped'])
    non_skipped_groups = [g for g in groups if g['status'] == 'reclaimable_now']
    total_non_skipped  = len(non_skipped_groups)
    lines = [
        '#!/bin/bash',
        '# auditorr — Dedupe Script',
        f'# Generated: {now_str}',
        '#',
        '# SUMMARY',
        f'# {len(groups)} duplicate groups found',
        f'# {_human_size(total_recoverable)} recoverable',
        f'# {skipped_count} groups blocked (cross-device or physical location unverified)',
    ]
    if excluded_count:
        lines.append(f'# {excluded_count} duplicate file(s) skipped — they match your Excluded Files & Folders settings')
    lines += [
        '#',
        '# This script replaces duplicate files with hardlinks.',
        '# All file paths will continue to exist after running.',
        '# All torrents will continue seeding normally.',
        '# Review each group carefully before running.',
        '#',
        '# USAGE:',
        f'#   cd <directory on your host that maps to {script_root}>',
        '#   bash dedupe.sh',
        '#',
        '# TIP: install "pv" (e.g. apt install pv) for a live progress bar while',
        '#      large files are verified — without it you get a heartbeat instead.',
        '#',
        f'# All paths are relative to {script_root} (auditorr\'s view).',
        '',
        f'TOTAL={total_non_skipped}',
        'DONE=0',
        'SKIPPED=0',
        'RECLAIMED=0',
        '',
        '# Re-verify two files are byte-identical before hardlinking. cmp has no',
        '# progress output of its own, so wrap it: a live progress bar via pv when',
        '# installed, otherwise a heartbeat so large-file checks are never silent.',
        'verify_identical() {',
        '  if command -v pv >/dev/null 2>&1; then',
        '    cmp -s <(pv -N "  comparing" "$1") "$2"',
        '  else',
        '    cmp -s "$1" "$2" &',
        '    local _pid=$!',
        '    while kill -0 "$_pid" 2>/dev/null; do printf "."; sleep 1; done',
        '    printf "\\n"',
        '    wait "$_pid"',
        '  fi',
        '}',
        '',
    ]

    # Working-directory guard using the first canonical file in a non-skipped group
    first_canon = next(
        (next(f for f in g['files'] if f['canonical']) for g in non_skipped_groups),
        None
    )
    if first_canon:
        qfirst = shlex.quote(first_canon['path'])
        lines += [
            f'FIRST_FILE={qfirst}',
            'if [ ! -e "$FIRST_FILE" ]; then',
            '  echo "ERROR: Cannot find files. Are you in the correct data directory?"',
            '  echo "  Expected to find: $FIRST_FILE"',
            '  echo "  cd into your parent data folder and try again."',
            '  exit 1',
            'fi',
            '',
        ]

    group_num = 0
    for g in groups:
        canonical     = next(f for f in g['files'] if f['canonical'])
        non_canonical = [f for f in g['files'] if not f['canonical']]
        filename      = os.path.basename(canonical['path'])
        if g['status'] != 'reclaimable_now':
            lines.append(f'# SKIPPED Group: {filename} — {g["blocker"] or g["status"]}')
            lines.append('')
            continue
        group_num += 1
        canon_path = canonical['path']
        lines.append(f'# Group {group_num}: {filename} — {_human_size(g["recoverable_size"])} recoverable')
        lines.append(f'# Canonical: {canon_path}')
        lines.append('GROUP_LINKED=0')
        for nc in non_canonical:
            nc_path    = nc['path']
            size_human = _human_size(nc['size'])
            size_bytes = nc['size']
            qcanon = shlex.quote(canon_path)
            qnc    = shlex.quote(nc_path)
            qname  = shlex.quote(filename)
            lines.append(f'# Duplicate: {nc_path}')
            lines.append(f'printf "[{group_num}/{total_non_skipped}] Verifying %s ({size_human})...\\n" {qname}')
            # cmp stops at the first differing byte — md5sum would read both
            # files in full (and hash them) even when they differ immediately.
            # verify_identical wraps cmp with a progress bar / heartbeat.
            lines.append(f'if ! verify_identical {qcanon} {qnc}; then')
            lines.append('  echo "  SKIP: Files differ — skipping this group"')
            lines.append('  SKIPPED=$((SKIPPED+1))')
            lines.append('else')
            lines.append('  echo "  Verified identical. Creating staged hardlink..."')
            lines.append(f'  LINK_TMP={qnc}.auditorr-link.$$.tmp')
            lines.append('  if [ -e "$LINK_TMP" ]; then')
            lines.append('    echo "  SKIP: Temporary link path already exists"')
            lines.append('    SKIPPED=$((SKIPPED+1))')
            lines.append(f'  elif ! ln -- {qcanon} "$LINK_TMP"; then')
            lines.append('    echo "  SKIP: Cannot hardlink across the backing filesystem"')
            lines.append('    SKIPPED=$((SKIPPED+1))')
            lines.append(f'  elif ! mv -f -- "$LINK_TMP" {qnc}; then')
            lines.append('    rm -f -- "$LINK_TMP"')
            lines.append('    echo "  SKIP: Could not atomically replace the duplicate"')
            lines.append('    SKIPPED=$((SKIPPED+1))')
            lines.append('  else')
            lines.append(f'    echo "  Done. {size_human} reclaimed."')
            lines.append(f'    RECLAIMED=$((RECLAIMED+{size_bytes}))')
            lines.append('    GROUP_LINKED=1')
            lines.append('  fi')
            lines.append('fi')
            lines.append('echo ""')
        lines.append('if [ "$GROUP_LINKED" -gt 0 ]; then DONE=$((DONE+1)); fi')
        lines.append('')
    lines.extend([
        'echo "================================"',
        'echo "Dedupe complete."',
        'echo "Groups processed: $DONE / $TOTAL"',
        'echo "Groups skipped (hash mismatch): $SKIPPED"',
        'echo ""',
        "echo \"Run 'df -h' to verify space reclaimed.\"",
    ])
    return '\n'.join(lines)

