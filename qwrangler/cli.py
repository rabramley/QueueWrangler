from __future__ import annotations

import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, ClassVar

import textual.widgets
from platformdirs import PlatformDirs
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen

platform_dirs = PlatformDirs("redrum", "lbrc")

DEFAULT_CONFIG: dict[str, Any] = {
    "music_path": platform_dirs.user_music_dir,
    "playlist_path": Path(platform_dirs.user_music_dir) / "playlists",
    "audio_suffixes": {".mp3", ".flac"},
    "playlist_suffixes": {".m3u", ".m3u8"},
}


def load_config() -> dict:
    path = Path(platform_dirs.user_config_dir) / "config.toml"

    if not path.is_file():
        return DEFAULT_CONFIG.copy()

    with path.open("rb") as file:
        return DEFAULT_CONFIG | tomllib.load(file)


class MediaTree(textual.widgets.DirectoryTree):
    """Directory tree showing folders, audio files, and M3U playlists."""

    def __init__(self, path: str | Path, **kwargs) -> None:
        super().__init__(path, **kwargs)
        self._folder_status_cache: dict[Path, str | None] = {}

    def filter_paths(
        self,
        paths: Iterable[Path],
    ) -> Iterable[Path]:
        return [
            path
            for path in paths
            if path.is_dir()
            or path.suffix.lower() in config["audio_suffixes"] | config["playlist_suffixes"]
        ]

    def clear_playlist_status_cache(self) -> None:
        self._folder_status_cache.clear()

    def invalidate_playlist_status(
        self,
        paths: Iterable[Path],
    ) -> None:
        """Invalidate folders containing changed tracks and their parents."""
        tree_root = Path(self.path).expanduser().resolve()
        affected_folders: set[Path] = set()

        for path in paths:
            resolved = path.expanduser().resolve()
            folder = resolved if resolved.is_dir() else resolved.parent

            while folder == tree_root or tree_root in folder.parents:
                affected_folders.add(folder)

                if folder == tree_root:
                    break

                folder = folder.parent

        for folder in affected_folders:
            self._folder_status_cache.pop(folder, None)

    def folder_playlist_status(
        self,
        folder: Path,
    ) -> str | None:
        """Return 'all', 'some', or None for a folder's audio files."""
        folder = folder.resolve()

        if folder in self._folder_status_cache:
            return self._folder_status_cache[folder]

        audio_files = {
            path.resolve()
            for path in folder.rglob("*")
            if path.is_file()
            and path.suffix.lower() in config["audio_suffixes"]
        }

        if not audio_files:
            status = None
        else:
            included_count = len(
                audio_files & self.app.playlist_tracks
            )

            if included_count == len(audio_files):
                status = "all"
            elif included_count:
                status = "some"
            else:
                status = None

        self._folder_status_cache[folder] = status
        return status

    def render_label(
        self,
        node: textual.widgets.TreeNode,
        base_style,
        style,
    ) -> Text:
        label = super().render_label(
            node,
            base_style,
            style,
        )

        if node.data is None:
            return label

        path = Path(node.data.path)

        if path.is_dir():
            status = self.folder_playlist_status(path)

            if status == "all":
                label.append(" ✓", style="bold green")
            elif status == "some":
                label.append(" ◐", style="bold yellow")

        elif (
            path.suffix.lower() in config["audio_suffixes"]
            and self.app.track_is_in_playlist(path)
        ):
            label.append(" ✓", style="bold green")

        return label


class TrackItem(textual.widgets.ListItem):
    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(textual.widgets.Label(path.name))


class SavePlaylistScreen(ModalScreen[Path | None]):
    CSS = """
    SavePlaylistScreen { align: center middle; }
    #dialog { width: 72; height: auto; padding: 1 2; border: round $accent; background: $surface; }
    #dialog Input { margin: 1 0; }
    #buttons { height: auto; align-horizontal: right; }
    #buttons Button { margin-left: 1; }
    """

    def __init__(self, initial: Path) -> None:
        super().__init__()
        self.initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield textual.widgets.Label("Save playlist as")
            yield textual.widgets.Input(value=str(self.initial), id="playlist-path")
            with Horizontal(id="buttons"):
                yield textual.widgets.Button("Cancel", id="cancel")
                yield textual.widgets.Button("Save", id="save", variant="primary")

    def on_mount(self) -> None:
        self.query_one(textual.widgets.Input).focus()

    @on(textual.widgets.Input.Submitted)
    def submit_input(self) -> None:
        self.save()

    @on(textual.widgets.Button.Pressed, "#save")
    def save(self) -> None:
        raw = self.query_one(textual.widgets.Input).value.strip()
        if not raw:
            self.notify("Enter a playlist filename", severity="warning")
            return
        path = Path(raw).expanduser()
        if path.suffix.lower() not in config["playlist_suffixes"]:
            path = path.with_suffix(".m3u")
        self.dismiss(path)

    @on(textual.widgets.Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class PlaylistApp(App[None]):
    TITLE = "Q Wrangler"
    CSS = """
        #main { height: 1fr; }
        #browser-panel { width: 1fr; border: round $primary; }
        #playlist-panel { width: 1fr; border: round $accent; }
        .panel-title { height: 3; padding: 1 2; text-style: bold; }
        MediaTree, ListView { height: 1fr; }
        #status { height: 3; padding: 1 2; background: $boost; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("enter", "activate", "Add / open", show=True),
        Binding("a", "add_folder", "Add folder", show=True),
        Binding("insert", "add_folder", "Add folder", show=True),
        Binding("delete", "remove_track", "Remove", show=True),
        Binding("u", "move_up", "Move up", show=True),
        Binding("j", "move_down", "Move down", show=True),
        Binding("ctrl+s", "save_playlist", "Save", show=True),
        Binding("ctrl+n", "new_playlist", "New", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.library: Path = Path(config["music_path"]).expanduser().resolve()
        self.playlists_path: Path = Path(config["playlist_path"]).expanduser().resolve()
        self.current_playlist_path: Path | None = None
        self.playlist_tracks: set[Path] = set()

    def track_is_in_playlist(self, path: Path) -> bool:
        return path.resolve() in self.playlist_tracks

    def refresh_track_icons(
        self,
        changed_paths: Iterable[Path] | None = None,
    ) -> None:
        browser = self.query_one("#browser", MediaTree)

        if not changed_paths:
            browser.clear_playlist_status_cache()
        else:
            browser.invalidate_playlist_status(changed_paths)

        # Textual caches rendered tree lines separately from folder statuses.
        browser._clear_line_cache()
        browser.refresh()

    def compose(self) -> ComposeResult:
        yield textual.widgets.Header()
        with Horizontal(id="main"):
            with Vertical(id="browser-panel"):
                yield textual.widgets.Static("Library", classes="panel-title")
                yield MediaTree(self.library, id="browser")
            with Vertical(id="playlist-panel"):
                yield textual.widgets.Static("Playlist", classes="panel-title")
                yield textual.widgets.ListView(id="playlist")
        yield textual.widgets.Static("Enter adds an audio file or opens an M3U playlist.", id="status")
        yield textual.widgets.Footer()

    @property
    def playlist(self) -> textual.widgets.ListView:
        return self.query_one("#playlist", textual.widgets.ListView)

    def tracks(self) -> list[Path]:
        return [item.path for item in self.playlist.children if isinstance(item, TrackItem)]

    def update_status(self, message: str | None = None) -> None:
        name = str(self.current_playlist_path) if self.current_playlist_path else "Unsaved playlist"
        detail = message or f"{len(self.tracks())} track(s)"
        self.query_one("#status", textual.widgets.Static).update(f"{name} | {detail}")

    @on(textual.widgets.DirectoryTree.FileSelected)
    def file_selected(self, event: textual.widgets.DirectoryTree.FileSelected) -> None:
        path = Path(event.path)
        if path.suffix.lower() in config["audio_suffixes"]:
            self.add_track(path)
        elif path.suffix.lower() in config["playlist_suffixes"]:
            self.load_playlist(path)

    def action_activate(self) -> None:
        focused = self.focused
        if isinstance(focused, MediaTree):
            node = focused.cursor_node
            if node is not None and node.data is not None:
                path = Path(node.data.path)
                if path.is_file():
                    if path.suffix.lower() in config["audio_suffixes"]:
                        self.add_track(path)
                    elif path.suffix.lower() in config["playlist_suffixes"]:
                        self.load_playlist(path)
        elif isinstance(focused, textual.widgets.ListView) and focused.highlighted_child is not None:
            self.notify(str(focused.highlighted_child.path))

    #
    # Add tracks to playlist
    #
    def add_track(self, path: Path) -> None:
        resolved = path.expanduser().resolve()

        if resolved in self.playlist_tracks:
            self.notify(
                f"Already present: {resolved.name}",
                severity="warning",
            )
            return

        self.playlist.append(TrackItem(resolved))
        self.playlist_tracks.add(resolved)

        self.refresh_track_icons([resolved])
        self.update_status(f"Added {resolved.name}")


    def action_add_folder(self) -> None:
        browser = self.query_one("#browser", MediaTree)
        node = browser.cursor_node

        if node is None or node.data is None:
            self.notify("Select a folder first", severity="warning")
            return

        selected_path = Path(node.data.path)

        if not selected_path.is_dir() and selected_path.suffix.lower() in config["audio_suffixes"]:
            self.add_track(selected_path)
            return

        existing_tracks = set(self.tracks())

        discovered_tracks = sorted(
            (
                path.resolve()
                for path in selected_path.rglob("*")
                if path.is_file()
                and path.suffix.lower() in config["audio_suffixes"]
            ),
            key=lambda path: path.as_posix().casefold(),
        )

        new_tracks = [
            path
            for path in discovered_tracks
            if path not in existing_tracks
        ]

        if not new_tracks:
            self.notify("No new files found", severity="warning")
            return

        self.playlist.extend(
            TrackItem(path)
            for path in new_tracks
        )

        self.playlist_tracks.update(new_tracks)
        self.refresh_track_icons(new_tracks)

        skipped = len(discovered_tracks) - len(new_tracks)
        message = f"Added {len(new_tracks)} track(s) from {selected_path.name}"

        if skipped:
            message += f"; skipped {skipped} duplicate(s)"

        self.update_status(message)
        self.notify(message)

    #
    # Edit playlist tracks
    #
    def action_remove_track(self) -> None:
        if self.playlist.has_focus:
            self._remove_playlist_track()
        elif self.query_one("#browser", MediaTree).has_focus:
            self._remove_track_library_item()

    def _remove_track_library_item(self) -> None:
        browser = self.query_one("#browser", MediaTree)
        node = browser.cursor_node

        if node is None or node.data is None:
            return

        selected_path = Path(node.data.path)

        if selected_path.is_file():
            self._remove_playlist_tracks([selected_path])
        elif selected_path.is_dir():
            discovered_tracks = {
                path.resolve()
                for path in selected_path.rglob("*")
                if path.is_file()
                and path.suffix.lower() in config["audio_suffixes"]
            }

            self._remove_playlist_tracks(discovered_tracks)

    def _remove_playlist_track(self) -> None:
        index = self.playlist.index
        item = self.playlist.highlighted_child

        if index is None or not isinstance(item, TrackItem):
            return

        removed_path = item.path
        self._remove_playlist_tracks([removed_path])

        self.call_after_refresh(
            self._restore_playlist_highlight,
            self.playlist.index or 0,
        )


    def _remove_playlist_tracks(self, paths: Iterable[Path]) -> None:
        removed_paths = set(paths) & self.playlist_tracks

        if not removed_paths:
            return

        self.playlist_tracks.difference_update(removed_paths)

        for item in list(self.playlist.children):
            if isinstance(item, TrackItem) and item.path in removed_paths:
                item.remove()

        self.refresh_track_icons(removed_paths)

        self.update_status(f"Removed {len(removed_paths)} track(s)")

    def action_move_up(self) -> None:
        self._move(-1)

    def action_move_down(self) -> None:
        self._move(1)

    def _move(self, offset: int) -> None:
        index = self.playlist.index
        items = list(self.tracks())
        if index is None or not items:
            return
        target = index + offset
        if target < 0 or target >= len(items):
            return
        items[index], items[target] = items[target], items[index]
        self.playlist.clear()
        self.playlist.extend(TrackItem(path) for path in items)

        self.call_after_refresh(
            self._restore_playlist_highlight,
            target,
        )

        self.update_status()

    def _restore_playlist_highlight(self, index: int) -> None:
        """Restore selection after deferred ListView DOM updates complete."""
        if not self.playlist.children:
            return

        self.playlist.index = min(index, len(self.playlist.children) - 1)
        self.playlist.focus()

    #
    # Manage playlist files
    #
    def load_playlist(self, path: Path) -> None:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            self.notify(f"Cannot read playlist: {exc}", severity="error")
            return
        tracks: list[Path] = []
        missing = 0
        for line in lines:
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            candidate = Path(entry).expanduser()
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            candidate = candidate.resolve()
            if candidate.suffix.lower() in config["audio_suffixes"] and candidate.is_file():
                tracks.append(candidate)
            else:
                missing += 1
        self.playlist.clear()
        self.playlist.extend(TrackItem(track) for track in tracks)
        self.current_playlist_path = path.resolve()
        suffix = f"; skipped {missing} missing/unsupported" if missing else ""

        self.playlist_tracks = set(tracks)
        self.refresh_track_icons()

        self.update_status(f"Loaded {len(tracks)} track(s){suffix}")

    def action_new_playlist(self) -> None:
        self.playlist.clear()
        self.current_playlist_path = None
        self.playlist_tracks.clear()

        self.refresh_track_icons()
        self.update_status("New playlist")

    def action_save_playlist(self) -> None:
        initial = self.current_playlist_path or (self.library / "playlist.m3u")
        self.push_screen(SavePlaylistScreen(initial), self._save_to)

    def _save_to(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            path = path.expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = ["#EXTM3U"]
            for track in self.tracks():
                try:
                    entry = track.relative_to(path.parent)
                except ValueError:
                    entry = track
                lines.append(entry.as_posix())
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            self.notify(f"Cannot save playlist: {exc}", severity="error")
            return
        self.current_playlist_path = path
        self.update_status(f"Saved {len(self.tracks())} track(s)")
        self.notify(f"Saved {path.name}")


def main() -> None:
    PlaylistApp().run()


config = load_config()

if __name__ == "__main__":
    main()
