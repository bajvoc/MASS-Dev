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
from collections.abc import AsyncGenerator, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from music_assistant_models.config_entries import ConfigEntry, ConfigValueType
from music_assistant_models.enums import (
    ConfigEntryType,
    ContentType,
    MediaType,
    ProviderFeature,
    StreamType,
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
    #    ProviderFeature.SEARCH,
    #    ProviderFeature.LIBRARY_ARTISTS,
    #    ProviderFeature.LIBRARY_ALBUMS,
    #    ProviderFeature.LIBRARY_PLAYLISTS,
    #    ProviderFeature.ARTIST_ALBUMS,
}


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
        files = await asyncio.to_thread(self.client.list, path)

        items = []
        for filename in files:
            # webdav3client returns filenames; you filter for music
            self.logger.debug(f"Found file: {filename}")
            if filename.lower().endswith((".mp3", ".flac", ".wav")):
                items.append(
                    Track(
                        item_id=f"{path}/{filename}".lstrip("/"),
                        provider=self.domain,
                        name=filename,
                    )
                )
        return items

    @use_cache
    async def get_track(self, item_id: str) -> Track:  # type: ignore[empty-body]
        """
        Get full track details by id.

        In this 'simple' version, item_id is just the relative path.
        Example: 'Rock/ACDC/Thunderstruck.mp3'
        """
        return Track(
            item_id=item_id,
            provider=self.domain,
            name=item_id.rsplit("/", maxsplit=1)[-1],  # Use filename as title for now
        )
        # Get full details of a single Track.
        # Mandatory only if you reported LIBRARY_TRACKS in the supported_features.
        # NOTE: Because this is often static data, it is advised to apply caching here
        # to avoid too many calls to the provider's API.
        # You can use the @use_cache decorator from music_assistant.controllers.cache
        # to easily apply caching to this method.

    async def get_library_tracks(self) -> AsyncGenerator[Track, None]:
        """Retrieve library tracks from the provider."""
        # OPTIONAL
        # Will only be called if you reported the LIBRARY_TRACKS feature
        # in the supported_features and you did not override the default sync method.
        # It allows retrieving the library/favorite tracks from your provider.
        # Warning: Async generator:
        # You should yield Track objects for each track in the library.
        # NOTE: This is only called on each full sync of the library (at the specified interval).
        # You are free to implement caching in your provider, as long as you return all items
        # on each call. The Music Assistant will take care of adding/removing items from the
        # library based on the returned items in the (default) 'sync_library' method.
        # If you need more fine grained control over the sync process, you can override
        # the 'sync_library' method.
        yield  # type: ignore[misc]

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """Still use direct_url for the actual playback."""
        return StreamDetails(
            provider=self.domain,
            item_id=item_id,
            audio_format=ContentType.try_parse(item_id.rsplit(".", maxsplit=1)[-1]),
            direct_url=f"{self.config.get_value('url')}/{item_id}",
        )

    # async def get_artist_albums(self, prov_artist_id: str) -> list[Album]:  # type: ignore[empty-body]
    #     """Get a list of all albums for the given artist."""
    #     # Get a list of all albums for the given artist.
    #     # Mandatory only if you reported ARTIST_ALBUMS in the supported_features.
    #     # NOTE: Because this is often static data, it is advised to apply caching here
    #     # to avoid too many calls to the provider's API.
    #     # You can use the @use_cache decorator from music_assistant.controllers.cache
    #     # to easily apply caching to this method.

    # async def get_album_tracks(  # type: ignore[empty-body]
    #     self,
    #     prov_album_id: str,
    # ) -> list[Track]:
    #     """Get album tracks for given album id."""
    #     # Get all tracks for a given album.
    #     # Mandatory only if you reported ARTIST_ALBUMS in the supported_features.
    #     # NOTE: Because this is often static data, it is advised to apply caching here
    #     # to avoid too many calls to the provider's API.
    #     # You can use the @use_cache decorator from music_assistant.controllers.cache
    #     # to easily apply caching to this method.

    # async def get_album(self, prov_album_id: str) -> Album:  # type: ignore[empty-body]
    #     """Get full album details by id."""
    #     # Get full details of a single Album.
    #     # Mandatory only if you reported LIBRARY_ALBUMS in the supported_features.
    #     # NOTE: Because this is often static data, it is advised to apply caching here
    #     # to avoid too many calls to the provider's API.
    #     # You can use the @use_cache decorator from music_assistant.controllers.cache
    #     # to easily apply caching to this method.

    # async def get_library_albums(self) -> AsyncGenerator[Album, None]:
    #     """Retrieve library albums from the provider."""
    #     # OPTIONAL
    #     # Will only be called if you reported the LIBRARY_ALBUMS feature
    #     # in the supported_features and you did not override the default sync method.
    #     # It allows retrieving the library/favorite albums from your provider.
    #     # Warning: Async generator:
    #     # You should yield Album objects for each album in the library.
    #     # NOTE: This is only called on each full sync of the library (at the specified interval).
    #     # You are free to implement caching in your provider, as long as you return all items
    #     # on each call. The Music Assistant will take care of adding/removing items from the
    #     # library based on the returned items in the (default) 'sync_library' method.
    #     # If you need more fine grained control over the sync process, you can override
    #     # the 'sync_library' method.
    #     yield  # type: ignore[misc]

    # async def get_artist(self, prov_artist_id: str) -> Artist:  # type: ignore[empty-body]
    #     """Get full artist details by id."""
    #     # Get full details of a single Artist.
    #     # Mandatory only if you reported LIBRARY_ARTISTS in the supported_features.
    #     # NOTE: Because this is often static data, it is advised to apply caching here
    #     # to avoid too many calls to the provider's API.
    #     # You can use the @use_cache decorator from music_assistant.controllers.cache
    #     # to easily apply caching to this method.

    # async def get_library_artists(self) -> AsyncGenerator[Artist, None]:
    #     """Retrieve library artists from the provider."""
    #     # OPTIONAL
    #     # Will only be called if you reported the LIBRARY_ARTISTS feature
    #     # in the supported_features and you did not override the default sync method.
    #     # It allows retrieving the library/favorite artists from your provider.
    #     # Warning: Async generator:
    #     # You should yield Artist objects for each artist in the library.
    #     # NOTE: This is only called on each full sync of the library (at the specified interval).
    #     # You are free to implement caching in your provider, as long as you return all items
    #     # on each call. The Music Assistant will take care of adding/removing items from the
    #     # library based on the returned items in the (default) 'sync_library' method.
    #     # If you need more fine grained control over the sync process, you can override
    #     # the 'sync_library' method.
    #     yield Artist(
    #         # A simple example of an artist object,
    #         # you should replace this with actual data from your provider.
    #         # Explore the Artist model for all options and descriptions.
    #         item_id="123",
    #         provider=self.instance_id,
    #         name="Artist Name",
    #         provider_mappings={
    #             ProviderMapping(
    #                 # A provider mapping is used to provide details about this item on this provider
    #                 # Music Assistant differentiates between domain and instance id to account for
    #                 # multiple instances of the same provider.
    #                 # The instance_id is auto generated by MA.
    #                 item_id="123",
    #                 provider_domain=self.domain,
    #                 provider_instance=self.instance_id,
    #                 # set 'available' to false if the item is (temporary) unavailable
    #                 available=True,
    #                 audio_format=AudioFormat(
    #                     # provide details here about sample rate etc. if known
    #                     content_type=ContentType.FLAC,
    #                 ),
    #             )
    #         },
    #     )

    # async def search(  # type: ignore[empty-body]
    #     self,
    #     search_query: str,
    #     media_types: list[MediaType],
    #     limit: int = 5,
    # ) -> SearchResults:
    #     """Perform search on musicprovider.

    #     :param search_query: Search query.
    #     :param media_types: A list of media_types to include.
    #     :param limit: Number of items to return in the search (per type).
    #     """
    #     # OPTIONAL
    #     # Will only be called if you reported the SEARCH feature in the supported_features.
    #     # It allows searching your provider for media items.
    #     # See the model for SearchResults for more information on what to return, but
    #     # in general you should return a list of MediaItems for each media type.
    #     # For radio, a simple search of the available channel names is acceptable

    # async def get_playlist(self, prov_playlist_id: str) -> Playlist:  # type: ignore[empty-body]
    #     """Get full playlist details by id."""
    #     # Get full details of a single Playlist.
    #     # Mandatory only if you reported LIBRARY_PLAYLISTS in the supported
    #     # NOTE: Because this is often static data, it is advised to apply caching here
    #     # to avoid too many calls to the provider's API.
    #     # You can use the @use_cache decorator from music_assistant.controllers.cache
    #     # to easily apply caching to this method.

    # async def get_library_playlists(self) -> AsyncGenerator[Playlist, None]:
    #     """Retrieve library/subscribed playlists from the provider."""
    #     # OPTIONAL
    #     # Will only be called if you reported the LIBRARY_PLAYLISTS feature
    #     # in the supported_features and you did not override the default sync method.
    #     # It allows retrieving the library/favorite playlists from your provider.
    #     # Warning: Async generator:
    #     # You should yield Playlist objects for each playlist in the library.
    #     # NOTE: This is only called on each full sync of the library (at the specified interval).
    #     # You are free to implement caching in your provider, as long as you return all items
    #     # on each call. The Music Assistant will take care of adding/removing items from the
    #     # library based on the returned items in the (default) 'sync_library' method.
    #     # If you need more fine grained control over the sync process, you can override
    #     # the 'sync_library' method.
    #     yield  # type: ignore[misc]

    # async def get_playlist_tracks(  # type: ignore[empty-body]
    #     self,
    #     prov_playlist_id: str,
    #     page: int = 0,
    # ) -> list[Track]:
    #     """Get all playlist tracks for given playlist id."""
    #     # Get all tracks for a given playlist.
    #     # Mandatory only if you reported LIBRARY_PLAYLISTS in the supported_features.
    #     # NOTE: It is advised to apply caching here (if possible)
    #     # to avoid too many calls to the provider's API.
    #     # You can use the @use_cache decorator from music_assistant.controllers.cache
    #     # to easily apply caching to this method.
