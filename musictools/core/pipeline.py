"""The one-click pipeline: convert → cover art → loudness → playlists → sync."""

import time

from . import art, convert, library, loudness, playlists, sync

STEPS = [
    ("convert", "Convert new FLACs to Opus"),
    ("art", "Copy cover art into Opus files"),
    ("loudness", "Write loudness (ReplayGain / R128) tags"),
    ("playlists", "Export playlists"),
    ("sync", "Sync to phone"),
]


def run(ctx, settings, steps):
    lib, opus = settings.library, settings.opus
    if not lib.is_dir():
        ctx.log(f"Library folder '{lib}' not found. Set it in Settings.", "error")
        return
    tracks = None

    def section(title):
        ctx.log("")
        ctx.log(f"━━ {title}", "head")

    if "convert" in steps:
        section("Convert")
        convert.convert(ctx, lib, opus, settings["bitrate"], settings["jobs"])
    if "art" in steps or "loudness" in steps:
        tracks = library.scan(lib, ctx, {".flac"})
    if "art" in steps:
        section("Cover art")
        art.copy_to_opus(ctx, tracks, lib, opus)
    if "loudness" in steps:
        section("Loudness")
        loudness.tag_library(ctx, tracks, lib, opus, jobs=settings["jobs"])
    if "playlists" in steps:
        section("Playlists")
        playlists.export(ctx, lib, opus)
    if "sync" in steps:
        section("Sync to phone")
        if not any(d.ready for d in sync.devices()):
            ctx.log("No phone connected — skipping sync.", "warn")
        else:
            plan = sync.make_plan(ctx, opus, settings["remote"], settings["keep"], settings["serial"])
            if plan is None:
                pass
            elif not plan.to_copy and not plan.to_delete:
                ctx.log("Phone is already up to date.", "ok")
                settings["last_sync"] = time.time()
            else:
                policy = settings["sync_confirm"]
                need_ask = policy == "always" or (policy == "deleting" and plan.to_delete)
                msg = f"Copy {len(plan.to_copy)} file(s) to the phone"
                msg += f" and delete {len(plan.to_delete)} file(s) from it?" if plan.to_delete else "?"
                if need_ask and not ctx.ask("Sync to phone", msg):
                    ctx.log("Sync skipped.", "warn")
                elif sync.execute(ctx, plan):
                    settings["last_sync"] = time.time()
            settings.save()
    ctx.log("")
    ctx.log("Pipeline finished.", "ok")
