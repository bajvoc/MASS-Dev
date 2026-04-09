"""WebDAV File System Provider constants."""

from typing import Final

# Only WebDAV-specific constants
# supported audio formats
AUDIO_FILES: Final[tuple[str, ...]] = (".mp3", ".flac", ".m4a", ".wav")
# WebDAV prefix for item_ids to differentiate from other providers and to easily parse the path
WEB_DAV: Final[str] = "webdav://rclone/music"
# WebDAV ignore folders
IGNORE_FOLDERS: Final[tuple[str, ...]] = (".thumbnails", "System Volume Information", "lost+found")
# supported audio formats
AUDIO_FILES: Final[tuple[str, ...]] = (".mp3", ".flac", ".m4a", ".wav")
# supported playlist formats
PLAYLIST_FILES: Final[tuple[str, ...]] = (".m3u", ".m3u8")
# base path for WebDAV items
BASE_PATH: Final[str] = "/Kasabian"
