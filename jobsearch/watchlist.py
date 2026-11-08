"""Starter watchlist: game and tech companies. Boards were confirmed against each feed on 2026-11-05.
Companies with no public feed stay on the list as "check manually" with a link to their careers page."""
from __future__ import annotations

from .models import WatchCompany

STARTER = [
    # Greenhouse
    WatchCompany("Riot Games", "greenhouse", "riotgames", "riotgames.com"),
    WatchCompany("Epic Games", "greenhouse", "epicgames", "epicgames.com"),
    WatchCompany("Roblox", "greenhouse", "roblox", "roblox.com"),
    WatchCompany("Discord", "greenhouse", "discord", "discord.com"),
    WatchCompany("Figma", "greenhouse", "figma", "figma.com"),
    WatchCompany("Bungie", "greenhouse", "bungie", "bungie.net"),
    WatchCompany("Naughty Dog", "greenhouse", "naughtydog", "naughtydog.com"),
    WatchCompany("Insomniac Games", "greenhouse", "insomniac", "insomniac.games"),
    WatchCompany("Twitch", "greenhouse", "twitch", "twitch.tv"),
    WatchCompany("Scopely", "greenhouse", "scopely", "scopely.com"),
    WatchCompany("2K", "greenhouse", "2k", "2k.com"),
    WatchCompany("Krafton", "greenhouse", "krafton", "krafton.com"),
    WatchCompany("Remedy Entertainment", "greenhouse", "remedy", "remedygames.com"),
    WatchCompany("Duolingo", "greenhouse", "duolingo", "duolingo.com"),
    WatchCompany("Vercel", "greenhouse", "vercel", "vercel.com"),
    # Lever
    WatchCompany("Kabam", "lever", "kabam", "kabam.com"),
    WatchCompany("Jam City", "lever", "jamcity", "jamcity.com"),
    WatchCompany("Spotify", "lever", "spotify", "spotify.com"),
    # Ashby
    WatchCompany("Notion", "ashby", "notion", "notion.so"),
    WatchCompany("Linear", "ashby", "linear", "linear.app"),
    WatchCompany("Supercell", "ashby", "supercell", "supercell.com"),
    WatchCompany("HoYoverse", "ashby", "hoyoverse", "hoyoverse.com"),
    WatchCompany("Second Dinner", "ashby", "seconddinner", "seconddinner.com"),
    # No public feed found: check these by hand
    WatchCompany("Supergiant Games", None, None, "supergiantgames.com", "https://www.supergiantgames.com/"),
    WatchCompany("Valve", None, None, "valvesoftware.com", "https://www.valvesoftware.com/en/jobs"),
    WatchCompany("Blizzard Entertainment", None, None, "blizzard.com", "https://careers.blizzard.com/"),
    WatchCompany("Unity", None, None, "unity.com", "https://unity.com/careers"),
    WatchCompany("Niantic", None, None, "nianticlabs.com", "https://nianticlabs.com/careers"),
    WatchCompany("Zynga", None, None, "zynga.com", "https://www.zynga.com/jobs/careers/"),
    WatchCompany("Mojang Studios", None, None, "minecraft.net", "https://www.minecraft.net/en-us"),
    WatchCompany("Respawn Entertainment", None, None, "respawn.com", "https://www.respawn.com/"),
    WatchCompany("Netflix", None, None, "netflix.com", "https://jobs.netflix.com/"),
    WatchCompany("Wizards of the Coast", None, None, "wizards.com", "https://company.wizards.com/en/careers"),
    WatchCompany("Moon Studios", None, None, "moonstudios.com", "https://www.moonstudios.com/"),
]
