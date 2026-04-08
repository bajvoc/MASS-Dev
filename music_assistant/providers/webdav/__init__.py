"""
DEMO/TEMPLATE Music Provider for Music Assistant.

This is an empty music provider with no actual implementation.
Its meant to get started developing a new music provider for Music Assistant.

Use it as a reference to discover what methods exists and what they should return.
Also it is good to look at existing music providers to get a better understanding,
due to the fact that providers may be flexible and support different features.

If you are relying on a third-party library to interact with the music source,
you can then reference your library in the manifest in the requirements section,
which is a list of (versioned!) python modules (pip syntax) that should be installed
when the provider is selected by the user.

Please keep in mind that Music Assistant is a fully async application and all
methods should be implemented as async methods. If you are not familiar with
async programming in Python, we recommend you to read up on it first.
If you are using a third-party library that is not async, you will need to use the
helper methods such as asyncio.to_thread or the create_task in the mass object to wrap
the calls to the library in a thread.

To add a new provider to Music Assistant, you need to create a new folder
in the providers folder with the name of your provider (e.g. 'my_music_provider').
In that folder you should create (at least) a __init__.py file and a manifest.json file.

As the provider gets bigger it is preferred to split it up. Start with __init__.py,
constants.py and provider.py. Other often used files are helpers.py, parsers.py and
streaming.py

Optional, but strongly desired, are icon.svg and icon_monochrome.svg files that will be used
as the icon for the provider in the UI, but if this is not possible then we also support
a material design icon in the manifest.json file.

IMPORTANT NOTE:
We strongly recommend developing on either macOS or Linux and start your development
environment by running the setup.sh script in the scripts folder of the repository.
This will create a virtual environment and install all dependencies needed for development.
See also our general DEVELOPMENT.md guide in the repository for more information.

"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncGenerator, Sequence
from datetime import datetime
from fileinput import filename
from typing import TYPE_CHECKING, final

import mutagen
from music_assistant_models.config_entries import ConfigEntry, ConfigValueType
from music_assistant_models.enums import (
    AlbumType,
    ConfigEntryType,
    ContentType,
    MediaType,
    ProviderFeature,
    StreamType,
)
from music_assistant_models.errors import (
    MediaNotFoundError,
)
from music_assistant_models.media_items import (
    Album,
    Artist,
    AudioFormat,
    BrowseFolder,
    ItemMapping,
    MediaItemType,
    Playlist,
    ProviderMapping,
    Radio,
    RecommendationFolder,
    SearchResults,
    Track,
)
from music_assistant_models.streamdetails import StreamDetails
from webdav3.client import Client as WebDavClient

from music_assistant.controllers.cache import use_cache
from music_assistant.models.music_provider import MusicProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType


SUPPORTED_FEATURES = {
    ProviderFeature.BROWSE,
    ProviderFeature.LIBRARY_TRACKS,
    ProviderFeature.LIBRARY_PLAYLISTS,
    #    ProviderFeature.SEARCH,
    #    ProviderFeature.LIBRARY_ARTISTS,
    #    ProviderFeature.LIBRARY_ALBUMS,
    #    ProviderFeature.ARTIST_ALBUMS,
}

# WebDAV constants
# WebDAV prefix for item_ids to differentiate from other providers and to easily parse the path
WEB_DAV: final = "webdav://"
# WebDAV ignore folders
IGNORE_FOLDERS: final[tuple[str, ...]] = (".thumbnails", "System Volume Information", "lost+found")
# supported audio formats
AUDIO_FILES: final[tuple[str, ...]] = (".mp3", ".flac", ".m4a", ".wav")
# supported playlist formats
PLAYLIST_FILES: final[tuple[str, ...]] = (".m3u", ".m3u8")


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    return WebDavProvider(mass, manifest, config, SUPPORTED_FEATURES)


async def get_config_entries(
    mass: MusicAssistant,
    instance_id: str | None = None,
    action: str | None = None,
    values: dict[str, ConfigValueType] | None = None,
) -> tuple[ConfigEntry, ...]:
    """
    Return Config entries for this provider.

    This generates the UI in the Music Assistant settings page.
    """
    return (
        ConfigEntry(
            key="url",
            type=ConfigEntryType.STRING,
            label="Server URL",
            required=True,
            description="The URL of your rclone/WebDAV server (e.g. http://192.168.1.10:8080)",
            default_value="http://127.0.0.1:8080",
        ),
        ConfigEntry(
            key="username",
            type=ConfigEntryType.STRING,
            label="Username",
            required=False,
            description="Optional: Only required if you enable authentication later.",
        ),
        ConfigEntry(
            key="password",
            type=ConfigEntryType.SECURE_STRING,
            label="Password",
            required=False,
            description="Optional: Only required if you enable authentication later.",
        ),
        ConfigEntry(
            key="verify_ssl",
            type=ConfigEntryType.BOOLEAN,
            label="Verify SSL",
            required=False,
            default_value=True,
            description="Disable this if using self-signed certificates on HTTPS.",
        ),
    )


class WebDavProvider(MusicProvider):
    """Simple WebDAV/HTTP provider for rclone development."""

    async def loaded_in_mass(self) -> None:
        """Initialize the client."""
        url = self.config.get_value("url").rstrip("/")
        username = self.config.get_value("username")
        password = self.config.get_value("password")

        # Define options - only include what is provided
        options = {
            "webdav_hostname": url,
        }

        # If user/pass are provided in settings, add them to the dict
        if username:
            options["webdav_login"] = username
        if password:
            options["webdav_password"] = password

        # Initialize the client
        self._client = WebDavClient(options)

        # Verify connection (rclone usually returns True for check() even if empty)
        # Since this library is sync, we run it in a thread
        try:
            connected = await asyncio.to_thread(self._client.check)
            if not connected:
                self.logger.warning(f"Could not verify connection to {url}")
            else:
                self.logger.debug(f"Connected to WebDAV at {url}")
        except Exception as err:
            self.logger.error(f"Error connecting to WebDAV: {err}")

    async def browse(self, path: str) -> list[Track]:
        """List files using the library."""
        # Use the library to get a list of files/folders
        # item_id is usually the relative path
        # Normalize path
        if path and path.startswith(WEB_DAV) and "http" not in path:
            current_path = path.replace(WEB_DAV, "", 1)
            self.logger.debug(f"Browsing path: {current_path}")
            files = await self._list_files(current_path)

            items = []
            for filename in files:
                # Skip the 'current' and 'parent' directory markers
                if filename not in (".", "..", "./", "../") and not filename.startswith("."):
                    self.logger.debug(f"Found file: {filename}")
                    directory = filename.endswith("/")

                    if directory:
                        items.append(
                            BrowseFolder(
                                item_id=f"{current_path}/{filename}".rstrip("/"),
                                provider=self.domain,
                                name=filename,
                            )
                        )
                    elif filename.lower().endswith(AUDIO_FILES):
                        track = await self._create_track(f"{current_path}/{filename}".rstrip("/"))
                        items.append(track)
                    elif filename.lower().endswith(PLAYLIST_FILES):
                        playlist = self._create_playlist(f"{current_path}/{filename}".rstrip("/"))
                        items.append(playlist)
        return items

    # @use_cache
    async def get_track(self, item_id: str) -> Track:  # type: ignore[empty-body]
        """
        Get full track details by id.

        In this 'simple' version, item_id is just the relative path.
        Example: 'Rock/ACDC/Thunderstruck.mp3'
        """
        self.logger.debug(f"get_track: item_id: {item_id}")
        item_id = item_id.replace(WEB_DAV, "", 1)

        return await self._create_track(item_id)

    async def get_library_tracks(self) -> AsyncGenerator[Track, None]:
        """Retrieve library tracks from the provider."""
        self.logger.debug("get_library_tracks: Syncing library tracks from WebDAV...")

        tracks = await self._browse("/")
        for track in tracks:
            yield track

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """Still use direct_url for the actual playback."""
        self.logger.debug(f"get_stream_details: item_id: {item_id} media_type: {media_type}")
        clean_path = item_id.replace(WEB_DAV, "", 1).lstrip("/")
        return StreamDetails(
            provider=self.domain,
            item_id=item_id,
            audio_format=AudioFormat(
                content_type=ContentType.try_parse(item_id.rsplit(".", maxsplit=1)[-1]),
            ),
            stream_type=StreamType.HTTP,
            path=f"{self.config.get_value('url')}/{clean_path}",
            can_seek=True,
            allow_seek=True,
        )

    async def get_album_tracks(self, item_id: str) -> list[Track]:
        """
        Return all tracks for a specific album.

        MASS calls this when you open an album 'page' or play an album.
        """
        # Extract the folder path from the album item_id
        self.logger.debug(f"get_album_tracks: item_id: {item_id}")
        album_path = item_id.replace(WEB_DAV, "", 1).lstrip("/")
        self.logger.debug(f"get_album_tracks: album_path: {album_path}")

        tracks = []

        try:
            # List files in that specific WebDAV directory
            items = await self._list_files(album_path)

            for item in items:
                # Skip directories and non-audio files
                if item.lower().endswith(AUDIO_FILES):
                    track_path = f"{album_path.rstrip('/')}/{item.lstrip('/')}"
                    track_obj = await self._create_track(track_path)
                    tracks.append(track_obj)

        except Exception as err:
            self.logger.error(
                f"get_album_tracks: Error fetching tracks for album {album_path}: {err}"
            )
            return []

        # Optional: Sort tracks by name/filename if no track number is present
        return sorted(tracks, key=lambda x: x.name)

    async def get_album(self, prov_album_id: str) -> Album:  # type: ignore[empty-body]
        """Get full album details by id."""
        self.logger.debug(f"get_album: prov_album_id: {prov_album_id}")
        clean_path = prov_album_id.replace(WEB_DAV, "", 1).lstrip("/").strip()
        if not clean_path:
            self.logger.warning("get_album: no album provided to search for")
            return None
        return await self._get_album(clean_path, None)

    async def get_artist(self, prov_artist_id: str) -> Artist:
        """Get full artist details by id."""
        self.logger.debug(f"get_artist: prov_artist_id: {prov_artist_id}")

        clean_path = prov_artist_id.replace(WEB_DAV, "", 1).lstrip("/").strip()
        if not clean_path:
            self.logger.warning("get_artist: no artist provided to search for")
            return None
        return await self._get_artist(clean_path, None)

    async def get_library_playlists(self) -> AsyncGenerator[Playlist, None]:
        """Retrieve library playlists from the provider."""
        self.logger.debug("get_library_playlists: Syncing library playlists from WebDAV...")

        playlists = await self._browse("/", browse_for=PLAYLIST_FILES)
        for playlist in playlists:
            yield playlist

    # @use_cache(3600 * 24 * 7)  # Cache for 7 days
    async def get_playlist(self, prov_playlist_id: str) -> Playlist:  # type: ignore[empty-body]
        """Get full playlist details by id."""
        self.logger.debug(f"get_playlist(): Adding track from playlist: {prov_playlist_id}")

        # 2. Return the Playlist object
        return self._create_playlist(prov_playlist_id)

    # @use_cache(3600 * 3)  # Cache for 3 hours
    async def get_playlist_tracks(
        self,
        prov_playlist_id: str,
        page: int = 0,
    ) -> list[Track]:
        """Get all playlist tracks for given playlist id."""
        self.logger.debug(f"get_playlist_tracks(): Parse playlist {prov_playlist_id}")
        if page > 0:
            # paging not supported, we always return the whole list at once
            return []
        clean_path = prov_playlist_id.replace(WEB_DAV, "", 1).lstrip("/")
        try:
            buffer = await self._download_from(clean_path)
            content = buffer.read().decode("utf-8")
            lines = content.splitlines()
        except Exception as err:
            self.logger.error(f"Failed to read playlist {prov_playlist_id}: {err}")
            return []

        tracks = []
        # Get the directory of the playlist to resolve relative paths
        base_dir = "/".join(clean_path.split("/")[:-1])

        for line in lines:
            line = line.strip()
            # Skip empty lines and M3U metadata/comments
            if not line or line.startswith("#"):
                continue

            # Resolve the path
            # If the playlist line is relative, prepend the base_dir
            if not (line.startswith(("http", "/"))):
                track_path = f"{base_dir}/{line}".lstrip("/")
            else:
                track_path = line.lstrip("/")

            # Create the Track object
            self.logger.debug(f"get_playlist_tracks(): Adding track from playlist: {track_path}")

            track = await self._create_track(track_path)
            tracks.append(track)

        return tracks

    async def _create_track(self, path: str) -> Track:
        """
        Create an Track object.

        Track will have Artist/Album metadata  parsed from a WebDAV path: /Artist/Album/Track.mp3
        """
        clean_path = path.replace(WEB_DAV, "", 1).lstrip("/")
        metadata = await self._get_track_metadata(clean_path)

        self.logger.debug(f"_create_track: Provider mapping path: {path}")
        # Create the unique Mapping
        mapping = ProviderMapping(
            item_id=f"{WEB_DAV}{clean_path}",
            provider_domain=self.domain,
            provider_instance=self.instance_id,
            audio_format=AudioFormat(
                content_type=ContentType.try_parse(metadata["audio_format"]),
            ),
        )
        self.logger.debug(f"_create_track: Provider mapping: {mapping.item_id}")

        # 4. Build the nested objects
        # Note: item_ids for Artists/Albums should also be prefixed for consistency
        artist_obj = self._create_artist(f"{WEB_DAV}{metadata.get('artist_id')}", metadata)
        album_obj = self._create_album(
            f"{WEB_DAV}{metadata.get('artist_id')}/{metadata.get('album_id')}",
            metadata,
            artist_obj,
        )
        self.logger.debug(f"_create_track: Path for track: {clean_path}")
        return Track(
            item_id=f"{WEB_DAV}{clean_path}",
            provider=self.domain,
            name=metadata.get("title"),
            artists=[artist_obj],
            album=album_obj,
            media_type=MediaType.TRACK,
            provider_mappings={mapping},
        )

    async def _get_track_metadata(self, path: str) -> dict:
        """
        Read metadata tags from the file.

        Read only the beginning of the file (64KB is usually enough for ID3v2)
        """
        metadata = {
            "title": "Unknown Title",
            "title_id": "",
            "artist": "Unknown Artist",
            "artist_id": "",
            "album": "Unknown Album",
            "album_id": "",
            "audio_format": None,
            "track_number": None,
            "year": None,
        }

        try:
            tags = None
            metadata_from_path = self._path_to_metadata(path)
            metadata.update(metadata_from_path)

            buffer = await self._download_from(path)

            # Use mutagen to parse the stream
            audio = mutagen.File(buffer)
            if audio and audio.tags:
                tags = audio.tags
                # Handle ID3 (MP3) vs Vorbis/FLAC (FLAC/OGG)
                if isinstance(tags, mutagen.id3.ID3):
                    metadata["title"] = str(tags.get("TIT2", "Unknown Title"))
                    metadata["artist"] = str(tags.get("TPE1", "Unknown Artist"))
                    metadata["album"] = str(tags.get("TALB", "Unknown Album"))
                    metadata["year"] = str(tags.get("TDRC", ""))[:4]
                else:
                    # Vorbis comments used by FLAC
                    metadata["title"] = tags.get("title", ["Unknown Title"])[0]
                    metadata["artist"] = tags.get("artist", ["Unknown Artist"])[0]
                    metadata["album"] = tags.get("album", ["Unknown Album"])[0]
                    metadata["year"] = tags.get("date", [""])[0][:4]
            else:
                self.logger.warning(
                    f"_get_track_metadata: No tags found for {path}. Fallback to filename parsing."
                )
                metadata["title"] = metadata_from_path.get("title_id").rsplit(".", 1)[0]
                metadata["artist"] = metadata_from_path.get("artist_id")
                metadata["album"] = metadata_from_path.get("album_id")
        except Exception as err:
            self.logger.warning(
                f"_get_track_metadata: Could not read tags for {path}: {err}. Fallback to filename parsing."
            )
            metadata["title"] = metadata_from_path.get("title_id").rsplit(".", 1)[0]
            metadata["artist"] = metadata_from_path.get("artist_id")
            metadata["album"] = metadata_from_path.get("album_id")
        except ValueError as err:
            self.logger.error(f"_get_track_metadata: Invalid path {path}: {err}")

        return metadata

    async def _read_metadata(self, path: str) -> dict:
        try:
            # List files in that specific WebDAV directory
            self.logger.debug(f"_read_metadata: Getting metadata for path: {path}")

            if path.lower().endswith(AUDIO_FILES):
                self.logger.debug(f"_read_metadata: Got match: {path.rstrip('/')}")
                metadata = await self._get_track_metadata(path.rstrip("/"))
            else:
                self.logger.error(f"_read_metadata: No music files found in {path}")
        except Exception as err:
            self.logger.error(f"_read_metadata:Error connecting to WebDAV: {err}")

        return metadata

    async def _list_files(self, path: str) -> list[str]:
        try:
            files = await asyncio.to_thread(self._client.list, path)
        except Exception as err:
            self.logger.error(f"Browse failed for {path}: {err}")
            files = []
        return files

    async def _browse(self, path: str, browse_for: tuple = AUDIO_FILES) -> list[Track]:
        """Browse a folder and return a list of Tracks."""
        self.logger.debug(f"_browse: Browsing path: {path}")
        items = await self._list_files(path)
        tracks = []
        for item in items:
            if item.startswith((".", "..")) or item in (IGNORE_FOLDERS):
                continue
                # debug remove after testing
                # if item.strip("/") in (
                #     "Bob Marley",
                #     "Chiki Liki Tu-A",
                #     "DIVOKEJ BILL - Unisono-Best Of 2000-2010 (CZ 2011)",
                # ):
                # continue
            if item.endswith("/"):
                sub_tracks = await self._browse(
                    f"{path.rstrip('/')}/{item.lstrip('/')}", browse_for
                )
                tracks.extend(sub_tracks)
                # TO BE REMOVED JUST FOR DEBUF PURPOSE
                # break
            if item.lower().endswith(browse_for):
                self.logger.debug(f"_browse: Browse for {browse_for}")
                if browse_for == AUDIO_FILES:
                    lib_item = await self._create_track(f"{path.rstrip('/')}/{item.lstrip('/')}")
                elif browse_for == PLAYLIST_FILES:
                    lib_item = self._create_playlist(f"{path.rstrip('/')}/{item.lstrip('/')}")
                if lib_item:
                    tracks.append(lib_item)
        return tracks

    async def _get_first_audio_file(self, path: str) -> str:
        """Get the first audio file in a folder."""
        self.logger.debug(f"_get_first_audio_file: Checking path: {path}")
        items = await self._list_files(path)
        filtered_items = [
            item
            for item in items
            if not item.startswith((".", "..")) and item.rstrip("/") not in (IGNORE_FOLDERS)
        ]
        self.logger.debug(f"_get_first_audio_file: Found {len(filtered_items)} items in {path}")
        match = next(
            (
                item
                for item in filtered_items
                if
                # It's an audio file
                (not item.endswith("/") and item.lower().endswith(AUDIO_FILES))
                or
                # It's a directory and NOT ignored/hidden
                (item.endswith("/") and not item.rstrip("/").startswith("."))
            ),
            None,
        )
        if match:
            if match.endswith("/"):
                self.logger.debug(
                    f"_get_first_audio_file: Browse in to path: {path}/{match if match else 'none'}"
                )
                match = await self._get_first_audio_file(f"{path.rstrip('/')}/{match.lstrip('/')}")
            else:
                match = f"{path.rstrip('/')}/{match.lstrip('/')}"
                self.logger.debug(f"_get_first_audio_file: Found file: {match}")
        return match

    async def _get_artist(self, path: str, metadata: dict) -> Artist:
        """Create an Artist object from metadata."""
        if not metadata:
            self.logger.debug("_get_artist: No metadata available, trying to read from files...")
            file = await self._get_first_audio_file(path)
            self.logger.debug(f"_get_artist: Reading metadata from first item: {file}")
            metadata = await self._read_metadata(f"{file}")

        item_id = f"{WEB_DAV}{metadata.get('artist_id')}"

        return self._create_artist(item_id, metadata)

    def _create_artist(self, item_id: str, metadata: dict) -> Artist:
        """Create an Artist object from metadata."""
        self.logger.debug(f"_create_artist: Artist: {metadata.get('artist')}, id: {item_id}")

        return Artist(
            item_id=item_id,
            provider=self.domain,
            name=metadata.get("artist"),
            provider_mappings={
                ProviderMapping(
                    item_id=item_id,
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
        )

    async def _get_album(self, path: str, metadata: dict) -> Album:
        """Create an Album object from metadata."""
        if not metadata:
            self.logger.debug("_get_album: No metadata available, trying to read from files...")
            file = await self._get_first_audio_file(f"{path}/")
            self.logger.debug(f"_get_album: Reading metadata from first item: {file}")
            metadata = await self._read_metadata(f"{file}")

        item_id = f"{WEB_DAV}{metadata.get('artist_id')}/{metadata.get('album_id')}"

        artist = self._create_artist(path, metadata)
        return self._create_album(item_id, metadata, artist)

    def _create_album(self, item_id: str, metadata: dict, artist: Artist) -> Album:
        """Create an Album object from metadata."""
        self.logger.debug(f"_create_album: Album {metadata.get('album')} id {item_id}")
        year = metadata.get("year")
        album = Album(
            item_id=item_id,
            provider=self.domain,
            name=metadata.get("album"),
            album_type=AlbumType.ALBUM,
            provider_mappings={
                ProviderMapping(
                    item_id=item_id,
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
            artists=[artist],
        )
        if year:
            album.year = year
        return album

    def _create_playlist(self, prov_playlist_id: str) -> Playlist:
        """Create a Playlist object from metadata."""
        play_list = prov_playlist_id.rsplit("/", maxsplit=1)[-1].rsplit(".", 1)[0]
        self.logger.debug(f"_create_playlist: Playlist {play_list} id {prov_playlist_id}")

        return Playlist(
            item_id=prov_playlist_id,
            provider=self.domain,
            name=play_list,
            provider_mappings={
                ProviderMapping(
                    item_id=prov_playlist_id,
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
            is_editable=False,  # WebDAV playlists are usually read-only via the API
        )

    async def _download_from(self, path: str) -> io.BytesIO:
        """Download a file from WebDAV into a buffer."""
        buffer = io.BytesIO()
        await asyncio.to_thread(self._client.download_from, buffer, path)
        buffer.seek(0)
        return buffer

    def _path_to_metadata(self, path: str) -> dict:
        """Extract metadata from the file path.

        Expecting a structure like: .../Artist/Album/Track.mp3
        After splitting the path, we expect Artist at index -3, Album at index -2, and Title at index -1.
        In case of missing parts, we fallback to 'Unknown Artist/Unknown Album' and use the filename as title.
        """
        metadata = {
            "artist_id": "Unknown Artist",
            "album_id": "Unknown Album",
            "title_id": "",
            "audio_format": None,
        }
        parts = path.split("/")
        self.logger.debug(f"_path_to_metadata: Track parts: {parts}")
        last_idx = len(parts) - 1
        artist_idx = last_idx - 2
        album_idx = last_idx - 1
        metadata["title_id"] = parts[last_idx]

        if metadata.get("title_id").lower().endswith(AUDIO_FILES):
            self.logger.debug(f"_path_to_metadata: Got audio file: {metadata.get('title_id')}")

            metadata["audio_format"] = metadata.get("title_id").split(".", 1)[1]
            if artist_idx >= 0:
                metadata["artist_id"] = parts[artist_idx]
            if album_idx >= 0:
                metadata["album_id"] = parts[album_idx]
        else:
            raise MediaNotFoundError(f"Path does not point to an audio file: {path}")
        return metadata
