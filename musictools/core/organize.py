"""Moving files around: template-based sorting/renaming and flattening folders.

When the files being moved are the FLAC library, their Opus versions are moved along with
them and playlists are updated, so nothing needs re-converting or ends up orphaned.
"""

import shutil
from pathlib import Path

from . import playlists
from .tags import render, write_tags
from .util import opus_path_for, remove_empty_dirs


def plan_moves(tracks, dest_root, template):
    """[(src, dst)] for each track, rendered from its tags. Clashes get ' (2)', ' (3)'…

    dest_root=None renames each file inside its own folder.
    """
    taken = set()
    moves = []
    for t in sorted(tracks, key=lambda t: str(t.path).lower()):
        root = Path(dest_root) if dest_root else t.path.parent
        rel = render(template, t.tags, t.path)
        dst = root / (rel + t.path.suffix.lower())
        n = 2
        while dst.as_posix().lower() in taken or (dst.exists() and dst.resolve() != t.path.resolve()):
            dst = root / f"{rel} ({n}){t.path.suffix.lower()}"
            n += 1
        taken.add(dst.as_posix().lower())
        if dst != t.path:
            moves.append((t.path, dst))
    return moves


def apply_moves(ctx, moves, copy=False, library=None, opus=None):
    """Move (or copy) files. If library/opus are given, Opus mirrors and playlists follow along."""
    done = {}
    mirror = library is not None and opus is not None
    for i, (src, dst) in enumerate(moves, 1):
        ctx.check()
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if copy:
                shutil.copy2(src, dst)
            elif src.resolve() != dst.resolve() and src.as_posix().lower() == dst.as_posix().lower():
                # case-only rename on a case-insensitive filesystem (Windows) needs a hop
                tmp = src.with_name(src.name + ".tmp-rename")
                src.rename(tmp)
                tmp.rename(dst)
            else:
                shutil.move(str(src), str(dst))
            done[src] = dst
            if mirror and not copy and src.suffix.lower() == ".flac":
                _move_opus(src, dst, library, opus)
            ctx.log(f"  {src.name}  →  {dst.name}", "ok")
        except Exception as e:
            ctx.log(f"  FAIL {src.name}: {e}", "error")
        ctx.progress(i, len(moves), f"{'Copying' if copy else 'Moving'} {i} / {len(moves)}")
    if mirror and not copy and done:
        n = playlists.remap(library, done)
        if n:
            ctx.log(f"Updated {n} playlist(s) with the new file names.")
        remove_empty_dirs(library)
        remove_empty_dirs(opus)
    return done


def save_tags(path, changes, library=None, opus=None):
    """Write tags to a file, and to its Opus version too when it is a library FLAC."""
    write_tags(path, changes)
    if library and opus and Path(path).suffix.lower() == ".flac":
        try:
            o = opus_path_for(path, library, opus)
        except ValueError:
            return
        if o.exists():
            write_tags(o, changes)


def _move_opus(old_flac, new_flac, library, opus):
    try:
        old_o = opus_path_for(old_flac, library, opus)
        new_o = opus_path_for(new_flac, library, opus)
    except ValueError:
        return
    if old_o.exists() and not new_o.exists():
        new_o.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_o), str(new_o))


def plan_flatten(target):
    target = Path(target)
    return sorted(p for p in target.rglob("*") if p.is_file() and p.parent != target)


def flatten(ctx, target, dry=False):
    """Move every file in subfolders up into target, then remove empty subfolders (was flattenplaylist.fish)."""
    target = Path(target)
    nested = plan_flatten(target)
    moved = clashes = 0
    for i, f in enumerate(nested, 1):
        ctx.check()
        dest = target / f.name
        if dest.exists():
            ctx.log(f"  skipped (already exists): {f.relative_to(target)}", "warn")
            clashes += 1
        elif dry:
            ctx.log(f"  would move: {f.relative_to(target)}")
            moved += 1
        else:
            shutil.move(str(f), str(dest))
            moved += 1
        ctx.progress(i, len(nested))
    if not dry:
        remove_empty_dirs(target)
    ctx.log(f"{'Would move' if dry else 'Moved'} {moved} file(s)"
            + (f", skipped {clashes} because a file with that name already exists." if clashes else "."), "ok")
