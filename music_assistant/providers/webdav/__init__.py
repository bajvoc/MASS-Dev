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

from typing import TYPE_CHECKING, final

from music_assistant_models.config_entries import ConfigEntry, ConfigValueType
from music_assistant_models.enums import (
    ConfigEntryType,
    ProviderFeature,
)

from .webdav_provider import WebDavProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType


SUPPORTED_FEATURES = {
    ProviderFeature.BROWSE,
    ProviderFeature.LIBRARY_TRACKS,
    #    ProviderFeature.LIBRARY_PLAYLISTS,
    #    ProviderFeature.SEARCH,
    #    ProviderFeature.LIBRARY_ARTISTS,
    #    ProviderFeature.LIBRARY_ALBUMS,
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
