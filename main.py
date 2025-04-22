import os
import re
import json
import time
import asyncio
import functools
from threading import Thread

from typing import Dict, List, Optional
from datetime import datetime, timedelta,timezone

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from flask import Flask
import threading

from gui import SwearGuardGUI
from swear_filter import SwearFilter, split_words
from shared import guild_filters
from database import (
    get_roles_data,
    save_roles_data,
    get_swear_data,
    save_swear_data,
    load_swear_data,
    load_guild_settings,
    log_violation,
    load_logging_channel, 
    save_logging_channel
)

command_cooldowns = {}  # user_id: last_command_time


# Bot setup
intents = discord.Intents.default()
intents.message_content = True  # Enable access to message content
bot = commands.Bot(command_prefix="!", intents=intents)
gui_system = SwearGuardGUI(bot)
app = Flask(__name__)
@app.route("/")
def home():
    return "✅ SwearBot is alive!", 200

def run():
    app.run(host="0.0.0.0", port=8080)

def start_keep_alive():
    threading.Thread(target=run).start()

#####################################
# Helper Functions
#####################################


async def send_log_message(guild: discord.Guild, user: discord.Member, message: str, channel: discord.TextChannel):
    """Send a formatted log message to the logging channel"""
    logging_channel_id = load_logging_channel(guild.id)
    if not logging_channel_id:
        return
    
    logging_channel = guild.get_channel(logging_channel_id)
    if not logging_channel:
        return
    
    # Get current time in UTC
    time_str = f"<t:{int(datetime.utcnow().timestamp())}:F>"
    
    embed = discord.Embed(
        title="🚨 Filtered Message",
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(name="User", value=f"{user.mention}\nID: {user.id}", inline=True)
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="Time", value=time_str, inline=False)
    embed.add_field(name="Message Content", value=f"```{message}```", inline=False)
    
    try:
        await logging_channel.send(embed=embed)
    except discord.Forbidden:
        print(f"Missing permissions to send messages in logging channel {logging_channel_id}")
        

def cooldown(seconds: int):
    """Professional embed-based cooldown with live countdown and auto-delete."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(interaction: discord.Interaction, *args, **kwargs):
            user_id = interaction.user.id
            now = datetime.now()
            last_time = command_cooldowns.get(user_id)

            if last_time:
                elapsed = (now - last_time).total_seconds()
                remaining = seconds - elapsed
                if remaining > 0:
                    remaining = int(remaining) + 1

                    try:
                        # Initial embed
                        embed = discord.Embed(
                            title="🚫 Cooldown",
                            description=f"Please wait ` {remaining} ` seconds before using this command again.",
                            color=discord.Color.from_str("#FF8C00")  # Soft but visible orange
                        )
                        embed.set_author(name="SwearFilter", icon_url=bot.user.avatar.url if bot.user.avatar else None)

                        await interaction.response.send_message(embed=embed, ephemeral=True)
                        message = await interaction.original_response()

                        # Countdown loop
                        for i in range(remaining - 1, 0, -1):
                            await asyncio.sleep(1)
                            embed.description = f"Please wait ` {i} ` seconds before using this command again."
                            await message.edit(embed=embed)

                        await asyncio.sleep(1)
                        await message.delete()
                    except Exception as e:
                        print(f"Cooldown embed error: {e}")
                    return

            command_cooldowns[user_id] = now
            return await func(interaction, *args, **kwargs)
        return wrapper
    return decorator

# Update the has_permission function in main.py:
async def has_permission(interaction: discord.Interaction) -> bool:
    """Check if user has permission to use admin commands."""
    # First check if the user is the actual guild owner
    if interaction.user.id == interaction.guild.owner_id:
        return True
    
    # Then check the allowed roles from database
    roles_data = get_roles_data(interaction.guild.id)
    
    # Check if user has any allowed roles
    user_role_ids = [str(r.id) for r in interaction.user.roles]
    allowed_role_ids = [str(r.id) for r in interaction.guild.roles if r.name in roles_data["allowed_roles"]]
    
    return any(role_id in allowed_role_ids for role_id in user_role_ids)

# Add to main.py

@bot.tree.command(name="setlog", description="Set the channel for logging filtered messages")
@cooldown(3)
async def set_logging_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Set the channel where filtered messages will be logged"""
    await interaction.response.defer(ephemeral=False)
    
    if not await has_permission(interaction):
        await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    if not channel.permissions_for(interaction.guild.me).send_messages:
        await interaction.followup.send("❌ I can't send messages in that channel. Please choose another one.", ephemeral=True)
        return

    if save_logging_channel(guild_id, channel.id):

        await interaction.followup.send(f"✅ Logging channel set to {channel.mention}")
    else:
        await interaction.followup.send("❌ Failed to set logging channel. Please try again.", ephemeral=True)

@bot.event
async def on_message(message):
    if message.author.bot or not isinstance(message.channel, discord.TextChannel):
        await bot.process_commands(message)
        return 

    try:
        guild_id = message.guild.id

        # Load config from Supabase
        swear_data = get_swear_data(guild_id)
        roles_data = get_roles_data(guild_id)

        # Initialize filter if missing
        if guild_id not in guild_filters:
            guild_filters[guild_id] = SwearFilter(swear_data["swear_words"])

        # Check if user is immune
        immune_role_ids = [str(r.id) for r in message.guild.roles if r.name in roles_data["immune_roles"]]
        user_role_ids = [str(r.id) for r in message.author.roles]
        is_immune = any(role_id in immune_role_ids for role_id in user_role_ids)

        # Skip check if immune or in allowed channel
        if is_immune or message.channel.id in swear_data["allowed_channels"]:
            await bot.process_commands(message)
            return

        # Check for swearing
        contains_swear = await guild_filters[guild_id].contains_swear_word(message.content)

        if contains_swear:
            try:
                await message.delete()
                now_utc = datetime.now(timezone.utc)
                # Log the violation with all required arguments
                log_violation(
                    guild_id=message.guild.id,
                    user_id=message.author.id,
                    username=message.author.name,
                    timestamp=now_utc.isoformat(),
                    discriminator=message.author.discriminator,
                    message=message.content,
                    channel_id=message.channel.id
                )

                # Send log message to logging channel
                logging_channel_id = load_logging_channel(guild_id)
                if logging_channel_id:
                    logging_channel = message.guild.get_channel(logging_channel_id)
                    if logging_channel:
                        time_str = f"<t:{int(datetime.utcnow().timestamp())}:F>"
                        embed = discord.Embed(
                            title="🚨 Filtered Message",
                            color=discord.Color.red(),
                            timestamp=datetime.utcnow()
                        )
                        embed.add_field(name="User", value=f"{message.author.mention}\nID: {message.author.id}", inline=True)
                        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                        embed.add_field(name="Time", value=time_str, inline=False)
                        embed.add_field(name="Message Content", value=f"```{message.content}```", inline=False)
                        try:
                            await logging_channel.send(embed=embed)
                        except discord.Forbidden:
                            print(f"Missing permissions to send messages in logging channel {logging_channel_id}")

                # Build warning message
                allowed_mentions = []
                for channel_id in swear_data["allowed_channels"]:
                    ch = message.guild.get_channel(channel_id)
                    if ch:
                        allowed_mentions.append(ch.mention)

                warning = (
                    f"{message.author.mention}, your message was filtered. "
                    f"Swearing is only allowed in: {' '.join(allowed_mentions)}"
                    if allowed_mentions else
                    f"{message.author.mention}, your message was filtered. Swearing is not allowed here."
                )

                await message.channel.send(warning, delete_after=10)

            except discord.Forbidden:
                print("❌ Missing permission to delete or send message.")
            except discord.NotFound:
                print("❌ Message already deleted.")
    except Exception as e:
        print(f"💥 on_message error: {e}")

    await bot.process_commands(message)
    
async def ensure_filter_initialized(guild_id: int):
    """Ensure the swear filter is initialized for a guild."""
    if guild_id not in guild_filters:
        swear_data = get_swear_data(guild_id)
        guild_filters[guild_id] = SwearFilter(swear_data["swear_words"])

#####################################
# Role Management Commands
#####################################

@bot.tree.command(name="addallowedrole", description="Add a role that can manage the bot")
@cooldown(3)
async def add_allowed_role(interaction: discord.Interaction, role: discord.Role):
    """Add a role to the allowed roles list."""
    await interaction.response.defer(ephemeral=False)
    
    if not await has_permission(interaction):
        await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    roles_data = get_roles_data(guild_id)
    
    if role.name in roles_data["allowed_roles"]:
        await interaction.followup.send(f"⚠️ {role.name} is already in the allowed roles list.", ephemeral=True)
        return
    
    roles_data["allowed_roles"].append(role.name)
    save_roles_data(guild_id, roles_data)
    
    await interaction.followup.send(f"✅ {role.name} has been added to the allowed roles list.")

@bot.tree.command(name="removeallowedrole", description="Remove management permission from a role")
@cooldown(3)
async def remove_allowed_role(interaction: discord.Interaction, role: discord.Role):
    """Remove a role from the allowed roles list."""
    await interaction.response.defer(ephemeral=False)
    
    if not await has_permission(interaction):
        await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    roles_data = get_roles_data(guild_id)
    
    if role.name not in roles_data["allowed_roles"]:
        await interaction.followup.send(f"⚠️ {role.name} is not in the allowed roles list.", ephemeral=True)
        return
    
    roles_data["allowed_roles"].remove(role.name)
    save_roles_data(guild_id, roles_data)
    
    await interaction.followup.send(f"✅ {role.name} has been removed from the allowed roles list.")

@bot.tree.command(name="addimmunerole", description="Add a role that is immune to swear filtering")
@cooldown(3)
async def add_immune_role(interaction: discord.Interaction, role: discord.Role):
    """Add a role to the immune roles list."""
    await interaction.response.defer(ephemeral=False)
    
    if not await has_permission(interaction):
        await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    roles_data = get_roles_data(guild_id)
    
    if role.name in roles_data["immune_roles"]:
        await interaction.followup.send(f"⚠️ {role.name} is already in the immune roles list.", ephemeral=True)
        return
    
    roles_data["immune_roles"].append(role.name)
    save_roles_data(guild_id, roles_data)
    
    await interaction.followup.send(f"✅ {role.name} has been added to the immune roles list.")

@bot.tree.command(name="removeimmunerole", description="Remove immunity from a role")
@cooldown(3)
async def remove_immune_role(interaction: discord.Interaction, role: discord.Role):
    """Remove a role from the immune roles list."""
    await interaction.response.defer(ephemeral=False)
    
    if not await has_permission(interaction):
        await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    roles_data = get_roles_data(guild_id)
    
    if role.name not in roles_data["immune_roles"]:
        await interaction.followup.send(f"⚠️ {role.name} is not in the immune roles list.", ephemeral=True)
        return
    
    roles_data["immune_roles"].remove(role.name)
    save_roles_data(guild_id, roles_data)
    
    await interaction.followup.send(f"✅ {role.name} has been removed from the immune roles list.")

@bot.tree.command(name="listroles", description="View all allowed and immune roles")
@cooldown(3)
async def list_roles(interaction: discord.Interaction):
    """List all allowed and immune roles."""
    await interaction.response.defer(ephemeral=False)
    
    roles_data = get_roles_data(interaction.guild.id)
    
    embed = discord.Embed(
        title="🛡️ **Role Management** 🛡️",
        description="Here are the current allowed and immune roles:",
        color=discord.Color.gold()
    )
    
    embed.add_field(
        name="Allowed Roles (Can manage bot)",
        value="\n".join(f"• {role}" for role in roles_data["allowed_roles"]) or "None",
        inline=False
    )
    
    embed.add_field(
        name="Immune Roles (Bypass filter)",
        value="\n".join(f"• {role}" for role in roles_data["immune_roles"]) or "None",
        inline=False
    )
    
    await interaction.followup.send(embed=embed)

#####################################
# Swear Word Management Commands
#####################################

@bot.tree.command(name="addswear", description="Add words to the swear filter")
@app_commands.describe(words="Words to add, separated by spaces or commas")
@cooldown(3)
async def add_swear(interaction: discord.Interaction, words: str):
    """Add words to the swear filter."""
    await interaction.response.defer(ephemeral=False)
    
    try:
        if not await has_permission(interaction):
            await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
            return
        
        guild_id = interaction.guild.id
        swear_data = get_swear_data(guild_id)
        
        # Initialize with empty list if none exists
        if not swear_data["swear_words"]:
            swear_data["swear_words"] = []

        words_to_add = split_words(words)
        added_words = [word for word in words_to_add if word not in swear_data["swear_words"]]
        
        if not added_words:
            await interaction.followup.send("⚠️ All specified words are already in the filter.", ephemeral=True)
            return
        
        swear_data["swear_words"].extend(added_words)
        save_swear_data(guild_id, swear_data)
        
        # Update filter immediately
        guild_filters[guild_id] = SwearFilter(swear_data["swear_words"])
        
        await interaction.followup.send(f"✅ Added `{', '.join(added_words)}` to the swear word list.")
    except Exception as e:
        print(f"Error in add_swear: {e}")
        await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

@bot.tree.command(name="removeswear", description="Remove words from the swear filter")
@app_commands.describe(words="Words to remove, separated by spaces or commas")
@cooldown(3)
async def remove_swear(interaction: discord.Interaction, words: str):
    """Remove words from the swear filter."""
    await interaction.response.defer(ephemeral=False)
    
    try:
        if not await has_permission(interaction):
            await interaction.followup.send("❌ You don't have permission!", ephemeral=True)
            return
        
        guild_id = interaction.guild.id
        swear_data = get_swear_data(guild_id)
        
        words_to_remove = split_words(words)
        removed_words = [word for word in words_to_remove if word in swear_data["swear_words"]]
        
        if not removed_words:
            await interaction.followup.send("⚠️ No matching words found in the filter.", ephemeral=True)
            return
        
        swear_data["swear_words"] = [word for word in swear_data["swear_words"] if word not in removed_words]
        save_swear_data(guild_id, swear_data)
        
        guild_filters[guild_id] = SwearFilter(swear_data["swear_words"])  # Update filter
        
        await interaction.followup.send(f"✅ Removed: `{', '.join(removed_words)}`")
    except Exception as e:
        print(f"Error in remove_swear: {e}")
        await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

@bot.tree.command(name="listswears", description="View all filtered words")
@cooldown(3)
async def list_swears(interaction: discord.Interaction):
    """List all filtered words with pagination."""
    await interaction.response.defer(ephemeral=False)
    
    try:
        guild_id = interaction.guild.id
        swear_data = get_swear_data(guild_id)
        
        if not swear_data["swear_words"]:
            await interaction.followup.send("ℹ️ The swear word list is currently empty.")
            return
            
        # Create paginated view for word list
        class WordsView(discord.ui.View):
            def __init__(self, words_list, original_interaction):
                super().__init__(timeout=180)  # Longer timeout for better UX
                self.words = words_list
                self.page = 0
                self.words_per_page = 15
                self.max_pages = (len(self.words) - 1) // self.words_per_page + 1
                self.original_interaction = original_interaction
                
            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return interaction.user == self.original_interaction.user
                
            async def on_timeout(self):
                for item in self.children:
                    item.disabled = True
                try:
                    await self.original_interaction.edit_original_response(view=self)
                except (discord.NotFound, discord.HTTPException):
                    pass
                
            @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.gray)
            async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.page = max(0, self.page - 1)
                await interaction.response.edit_message(embed=self.get_embed())
                
            @discord.ui.button(label="▶️ Next", style=discord.ButtonStyle.gray)
            async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.page = min(self.max_pages - 1, self.page + 1)
                await interaction.response.edit_message(embed=self.get_embed())
                
            def get_embed(self):
                start_idx = self.page * self.words_per_page
                end_idx = min(start_idx + self.words_per_page, len(self.words))
                
                embed = discord.Embed(
                    title="📜 **Filtered Words List** 📜",
                    description="Here are the words currently being filtered:",
                    color=discord.Color.red()
                )
                
                word_list = "\n".join(f"• {word}" for word in self.words[start_idx:end_idx])
                embed.add_field(name=f"Words ({start_idx+1}-{end_idx} of {len(self.words)})", 
                              value=word_list, inline=False)
                
                embed.set_footer(text=f"Page {self.page+1}/{self.max_pages} • Use the buttons to navigate")
                return embed
        
        view = WordsView(sorted(swear_data["swear_words"]), interaction)
        await interaction.followup.send(embed=view.get_embed(), view=view)
    except Exception as e:
        print(f"Error in list_swears: {e}")
        await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

#####################################
# Channel Management Commands
#####################################

@bot.tree.command(name="setallowedswear", description="Allow swearing in a specific channel")
@cooldown(3)
async def set_allowed_swear(interaction: discord.Interaction, channel: discord.TextChannel):
    """Allow swearing in a specific channel."""
    await interaction.response.defer(ephemeral=False)
    
    try:
        if not await has_permission(interaction):
            await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
            return
        
        guild_id = interaction.guild.id
        swear_data = get_swear_data(guild_id)
        
        if channel.id in swear_data["allowed_channels"]:
            await interaction.followup.send(f"⚠️ Swearing is already allowed in {channel.mention}.", ephemeral=True)
            return
        
        swear_data["allowed_channels"].append(channel.id)
        save_swear_data(guild_id, swear_data)
        
        await interaction.followup.send(f"✅ Swearing is now allowed in {channel.mention}.")
    except Exception as e:
        print(f"Error in set_allowed_swear: {e}")
        await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

@bot.tree.command(name="unsetallowedswear", description="Remove swear permission from a channel")
@cooldown(3)
async def unset_allowed_swear(interaction: discord.Interaction, channel: discord.TextChannel):
    """Remove swear permission from a channel."""
    await interaction.response.defer(ephemeral=False)
    
    try:
        if not await has_permission(interaction):
            await interaction.followup.send("❌ You do not have permission to use this command.", ephemeral=True)
            return
        
        guild_id = interaction.guild.id
        swear_data = get_swear_data(guild_id)
        
        if channel.id not in swear_data["allowed_channels"]:
            await interaction.followup.send(f"⚠️ Swearing is not allowed in {channel.mention}.", ephemeral=True)
            return
        
        swear_data["allowed_channels"].remove(channel.id)
        save_swear_data(guild_id, swear_data)
        
        await interaction.followup.send(f"✅ Swearing is no longer allowed in {channel.mention}.")
    except Exception as e:
        print(f"Error in unset_allowed_swear: {e}")
        await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

@bot.tree.command(name="listallowed", description="Show all channels where swearing is allowed")
@cooldown(3)
async def list_allowed(interaction: discord.Interaction):
    """List all channels where swearing is allowed."""
    await interaction.response.defer(ephemeral=False)
    
    try:
        guild_id = interaction.guild.id
        swear_data = get_swear_data(guild_id)
        
        channels = [f"<#{channel_id}>" for channel_id in swear_data["allowed_channels"]]
        embed = discord.Embed(
            title="📜 **Allowed Swear Channels** 📜",
            description="Here are the channels where swearing is allowed:",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Allowed Channels",
            value="\n".join(channels) or "None",
            inline=False
        )
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"Error in list_allowed: {e}")
        await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

#####################################
# Testing and Help Commands
#####################################

@bot.tree.command(name="testswear", description="Test if a message would be filtered")
@cooldown(3)
async def test_swear(interaction: discord.Interaction, message: str):
    """Test if a message would be filtered."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        guild_id = interaction.guild.id
        await ensure_filter_initialized(guild_id)
        
        if await guild_filters[guild_id].contains_swear_word(message):
            await interaction.followup.send("⚠️ This message contains filtered words and would be deleted.", ephemeral=True)
        else:
            await interaction.followup.send("✅ This message would be allowed.", ephemeral=True)
    except Exception as e:
        print(f"Error in test_swear: {e}")
        await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

@bot.tree.command(name="helpswear", description="Show help for managing the SwearFilter bot")
@cooldown(3)
async def helpswear_command(interaction: discord.Interaction):
    """Clean and professional help menu for SwearFilter"""
    help_pages = [
        {
            "title": "SwearFilter Help: Overview",
            "description": (
                "**SwearFilter** helps manage server language with ease.\n"
                "Use the visual dashboard or commands to configure everything."
            ),
            "color": discord.Color.blurple(),
            "thumbnail": "https://i.postimg.cc/0NW2STCd/logo.png",
            "fields": [
                {
                    "name": "Quick Start",
                    "value": (
                        "`/sweargui` — Open the visual dashboard\n"
                        "`/testswear [message]` — Check if a message is filtered\n"
                        "`/listswears` — View all filtered words\n"
                        "`/listallowed` — View allowed channels\n"
                        "`/helpswear` — Show this help menu"
                    ),
                    "inline": False
                }
            ]
        },
        {
            "title": "SwearFilter Help: Roles",
            "description": "Manage who can access or bypass the filter.",
            "color": discord.Color.green(),
            "thumbnail": "https://i.postimg.cc/0NW2STCd/logo.png",
            "fields": [
                {
                    "name": "Management Roles",
                    "value": (
                        "`/addallowedrole [role]` — Allow this role to manage SwearFilter\n"
                        "`/removeallowedrole [role]` — Remove management permission\n"
                        "`/listroles` — List all management and immune roles"
                    ),
                    "inline": False
                },
                {
                    "name": "Immune Roles",
                    "value": (
                        "`/addimmunerole [role]` — Let role bypass the filter\n"
                        "`/removeimmunerole [role]` — Remove immunity"
                    ),
                    "inline": False
                }
            ]
        },
        {
            "title": "SwearFilter Help: Word Filtering",
            "description": "Control which words are filtered from your server.",
            "color": discord.Color.red(),
            "thumbnail": "https://i.postimg.cc/0NW2STCd/logo.png",
            "fields": [
                {
                    "name": "Word Management",
                    "value": (
                        "`/addswear [words]` — Add words to the filter\n"
                        "`/removeswear [words]` — Remove words from the filter\n"
                        "`/listswears` — View all filtered words"
                    ),
                    "inline": False
                }
            ]
        },
        {
            "title": "SwearFilter Help: Channels & Logging",
            "description": "Customize where swearing is allowed and where logs are sent.",
            "color": discord.Color.gold(),
            "thumbnail": "https://i.postimg.cc/0NW2STCd/logo.png",
            "fields": [
                {
                    "name": "Channel Settings",
                    "value": (
                        "`/setallowedswear [channel]` — Allow swearing in a channel\n"
                        "`/unsetallowedswear [channel]` — Remove swearing permission\n"
                        "`/listallowed` — View allowed channels"
                    ),
                    "inline": False
                },
                {
                    "name": "Logging",
                    "value": (
                        "`/setlog [channel]` — Set log destination for filtered messages\n"
                        "Each log includes: user, channel, time, and message content."
                    ),
                    "inline": False
                }
            ]
        }
    ]

    class HelpView(discord.ui.View):
        def __init__(self, interaction: discord.Interaction):
            super().__init__(timeout=300)
            self.page = 0
            self.interaction = interaction
            self.update_buttons()

        def update_buttons(self):
            self.prev_button.disabled = self.page == 0
            self.next_button.disabled = self.page == len(help_pages) - 1
            self.page_indicator.label = f"{self.page + 1}/{len(help_pages)}"

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            return interaction.user == self.interaction.user

        def create_embed(self):
            page = help_pages[self.page]
            embed = discord.Embed(
                title=page["title"],
                description=page["description"],
                color=page["color"]
            )
            embed.set_thumbnail(url=page["thumbnail"])
            for field in page["fields"]:
                embed.add_field(
                    name=field["name"],
                    value=field["value"],
                    inline=field.get("inline", False)
                )
            return embed

        @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
        async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.page = 0
            self.update_buttons()
            await interaction.response.edit_message(embed=self.create_embed(), view=self)

        @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
        async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.page = max(0, self.page - 1)
            self.update_buttons()
            await interaction.response.edit_message(embed=self.create_embed(), view=self)

        @discord.ui.button(label="1/4", style=discord.ButtonStyle.gray, disabled=True)
        async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
            pass

        @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
        async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.page = min(len(help_pages) - 1, self.page + 1)
            self.update_buttons()
            await interaction.response.edit_message(embed=self.create_embed(), view=self)

        @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
        async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.page = len(help_pages) - 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.create_embed(), view=self)

        @discord.ui.button(label="GUI Dashboard", style=discord.ButtonStyle.success, row=1)
        async def gui_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.defer()
            await gui_system.create_dashboard(interaction)

        @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=1)
        async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.message.delete()

    try:
        view = HelpView(interaction)
        await interaction.response.send_message(embed=view.create_embed(), view=view)
    except Exception as e:
        print(f"Help command error: {e}")
        await interaction.response.send_message(
            "❌ Could not load the help menu. Use `/sweargui` to access the dashboard.",
            ephemeral=True
        )

@bot.tree.command(name="sweargui")
@cooldown(3)
async def swear_gui(interaction: discord.Interaction):
    """Open the GUI dashboard for swear filter management."""
    await interaction.response.defer(ephemeral=False)
    
    try:
        await gui_system.create_dashboard(interaction)
    except Exception as e:
        print(f"Error in swear_gui: {e}")
        await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

@bot.event
async def on_message(message):
    # Ignore bot messages and non-text channels
    if message.author.bot or not isinstance(message.channel, discord.TextChannel):
        await bot.process_commands(message)
        return

    try:
        guild_id = message.guild.id
        swear_data = get_swear_data(guild_id)
        roles_data = get_roles_data(guild_id)

        # Initialize filter if needed
        if guild_id not in guild_filters:
            guild_filters[guild_id] = SwearFilter(swear_data["swear_words"])

        # Check immunity
        immune_roles = [r.name for r in message.guild.roles if r.name in roles_data["immune_roles"]]
        is_immune = any(role.name in immune_roles for role in message.author.roles)

        # Skip if immune or in allowed channel
        if is_immune or message.channel.id in swear_data["allowed_channels"]:
            await bot.process_commands(message)
            return

        # Check for swear words
        if await guild_filters[guild_id].contains_swear_word(message.content):
            try:
                await message.delete()
                
                # Get current time in UTC
                now_utc = datetime.now(timezone.utc)
                timestamp_str = now_utc.isoformat()
                discord_time = f"<t:{int(now_utc.timestamp())}:F>"
                
                # Log to database
                log_violation(
                    guild_id=guild_id,
                    user_id=message.author.id,
                    username=message.author.name,
                    discriminator=getattr(message.author, 'discriminator', '0'),
                    message=message.content,
                    channel_id=message.channel.id,
                    timestamp=timestamp_str
                )

                # Send to logging channel
                if logging_channel_id := load_logging_channel(guild_id):
                    if logging_channel := message.guild.get_channel(logging_channel_id):
                        embed = discord.Embed(
                            title="🚨 Filtered Message",
                            color=discord.Color.red(),
                            timestamp=now_utc
                        )
                        embed.add_field(
                            name="User", 
                            value=f"{message.author.mention}\n({message.author.name})",
                            inline=True
                        )
                        embed.add_field(
                            name="Channel", 
                            value=message.channel.mention,
                            inline=True
                        )
                        embed.add_field(
                            name="Time", 
                            value=discord_time, 
                            inline=False
                        )
                        embed.add_field(
                            name="Message Content", 
                            value=f"```{message.content[:1000]}```", 
                            inline=False
                        )
                        embed.set_footer(text=f"User ID: {message.author.id}")
                        
                        try:
                            await logging_channel.send(embed=embed)
                        except discord.Forbidden:
                            print(f"Missing permissions to log to {logging_channel.mention}")

                # Send warning message
                allowed_channels = [
                    f"<#{cid}>" for cid in swear_data["allowed_channels"]
                    if message.guild.get_channel(cid)
                ]
                warning = (
                    f"{message.author.mention}, your message was filtered. "
                    f"Swearing is only allowed in: {' '.join(allowed_channels)}"
                    if allowed_channels else
                    f"{message.author.mention}, your message was filtered. Swearing is not allowed here."
                )
                
                
                try:
                    await message.channel.send(warning, delete_after=10)
                except discord.Forbidden:
                    pass

            except discord.NotFound:
                pass  # Message already deleted
            except discord.Forbidden:
                print(f"Missing permissions in {message.channel.name}")

    except Exception as e:
        print(f"Error processing message: {e}")

    # Fix for the TypeError - removed accidental @ symbol
    await bot.process_commands(message)
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"Bot is in {len(bot.guilds)} guilds")
    
    
    # Initialize filters for all guilds
    for guild in bot.guilds:
        guild_id = guild.id
        swear_data = get_swear_data(guild_id)
        print(f"Initializing filter for {guild.name} with words: {swear_data['swear_words']}")
        guild_filters[guild_id] = SwearFilter(swear_data["swear_words"])
    
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands successfully!")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")

@bot.event
async def on_guild_join(guild):
    """Initialize filter and send DM setup guide to the owner."""
    guild_id = guild.id
    swear_data = get_swear_data(guild_id)
    guild_filters[guild_id] = SwearFilter(swear_data["swear_words"])
    print(f"✅ Joined {guild.name} — initialized filter.")

    try:
        owner = await bot.fetch_user(guild.owner_id)
        if not owner:
            return

        pages = [
            {
                "title": "👋 Welcome to SwearFilter!",
                "desc": (
                    "Thanks for adding **SwearFilter** — your server’s new profanity guardian. 💂‍♂️\n\n"
                    "We’ll walk you through setup in just a minute.\n\n"
                    "Use `/sweargui` to open the visual dashboard, or follow the steps below."
                ),
                "fields": [
                    ("📌 First Step", "`/setlog [channel]` — where filtered messages will be logged"),
                    ("📖 Need Help?", "`/helpswear` — full command list & dashboard tips")
                ],
                "color": discord.Color.blurple()
            },
            {
                "title": "🛠️ Logging & Permissions",
                "desc": "Control where logs are sent and who can manage or bypass the bot.",
                "fields": [
                    ("📝 Set Log Channel", "`/setlog [#channel]`\nWhere filtered messages get logged."),
                    ("✅ Allow Manager Role", "`/addallowedrole [role]`\nLet a role use bot setup commands."),
                    ("🛑 Immune Roles", "`/addimmunerole [role]`\nPeople with this role can swear freely."),
                    ("📋 View Roles", "`/listroles`\nSee current managers"
                    " and immune roles.")
                ],
                "color": discord.Color.teal()
            },
            
            {
                "title": "🧹 Filter Words & Channels",
                "desc": "Customize which words get filtered and where swearing is allowed.",
                "fields": [
                    ("➕ Add Swears", "`/addswear [badword1, badword2]`"),
                    ("➖ Remove Swears", "`/removeswear [word]`"),
                    ("📃 List Swears", "`/listswears`"),
                    ("✅ Allow Channel", "`/setallowedswear [channel]`\nLet users swear freely in this channel."),
                    ("🚫 Unallow Channel", "`/unsetallowedswear [channel]`\nRemove swearing permission from that channel."),
                    ("📋 View Channels", "`/listallowed`\nSee where swearing is currently allowed.")
                ],
                "color": discord.Color.red()
            },
            {
                "title": "📊 Monitor & Test",
                "desc": "Make sure it’s working perfectly — test and observe.",
                "fields": [
                    ("🧪 Test Message", "`/testswear [message]`"),
                    ("👁️ Check Logs", "See the log channel you set for filtered message reports"),
                    ("🖥️ Dashboard Access", "`/sweargui` to launch the visual dashboard")
                ],
                "color": discord.Color.gold()
            }
        ]

        class OnboardingView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=300)
                self.page = 0
                self.max_page = len(pages) - 1
                self.update_buttons()

            def build_embed(self):
                page = pages[self.page]
                embed = discord.Embed(
                    title=page["title"],
                    description=page["desc"],
                    color=page["color"]
                )
                for name, value in page["fields"]:
                    embed.add_field(name=name, value=value, inline=False)
                embed.set_footer(text=f"Page {self.page + 1} of {len(pages)}")
                return embed

            def update_buttons(self):
                self.first.disabled = self.page == 0
                self.prev.disabled = self.page == 0
                self.next.disabled = self.page == self.max_page
                self.last.disabled = self.page == self.max_page
                self.page_info.label = f"{self.page + 1}/{len(pages)}"

            @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
            async def first(self, interaction: discord.Interaction, _):
                self.page = 0
                self.update_buttons()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
            async def prev(self, interaction: discord.Interaction, _):
                self.page -= 1
                self.update_buttons()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            @discord.ui.button(label="1/4", style=discord.ButtonStyle.gray, disabled=True)
            async def page_info(self, interaction: discord.Interaction, _): pass

            @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
            async def next(self, interaction: discord.Interaction, _):
                self.page += 1
                self.update_buttons()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
            async def last(self, interaction: discord.Interaction, _):
                self.page = self.max_page
                self.update_buttons()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            @discord.ui.button(label="GUI Dashboard", style=discord.ButtonStyle.success, row=1)
            async def gui_button(self, interaction: discord.Interaction, _):
                await interaction.response.defer()
                await gui_system.create_dashboard(interaction)

            @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=1)
            async def close_button(self, interaction: discord.Interaction, _):
                await interaction.message.delete()

        await owner.send(
            content="✨ **Welcome to SwearFilter** — let’s get you set up!",
            embed=OnboardingView().build_embed(),
            view=OnboardingView()
        )

    except discord.Forbidden:
        print(f"❌ Cannot DM the owner of {guild.name} — DMs likely disabled.")
    except Exception as e:
        print(f"💥 Error sending onboarding DM: {e}")

@bot.command()
async def sync(ctx):
    if ctx.author.guild_permissions.administrator:
        try:
            await ctx.message.add_reaction('⏳')  # Processing reaction
            synced = await bot.tree.sync()
            await ctx.message.remove_reaction('⏳', bot.user)
            await ctx.message.add_reaction('✅')  # Success reaction
            await ctx.send(f"✅ Synced {len(synced)} slash commands successfully!")
        except Exception as e:
            await ctx.message.remove_reaction('⏳', bot.user)
            await ctx.message.add_reaction('❌')  # Error reaction
            await ctx.send(f"❌ Error syncing commands: {e}")
    else:
        await ctx.send("❌ You need admin permissions to use this.")

# Error handler for app commands
@bot.event
async def on_application_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    error_message = f"An error occurred with the command: {str(error)}"
    
    # If we haven't responded yet, defer the interaction first
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(f"❌ {error_message}", ephemeral=True)
    else:
        try:
            await interaction.followup.send(f"❌ {error_message}", ephemeral=True)
        except:
            # If we can't followup (e.g., interaction is too old), just log the error
            print(f"Command error could not be sent: {error_message}")
    
    # Log the error details
    print(f"Command error: {error}")

# Start the web server

# Run the bot
if __name__ == "__main__":
    load_dotenv()  # Loads the .env file
    start_keep_alive() 
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        print("ERROR: No Discord token found in environment variables!")
        exit(1)
    
    try:
        bot.run(token)
    except discord.LoginFailure:
        print("ERROR: Invalid Discord token!")
    except Exception as e:
        print(f"ERROR: Failed to start bot: {e}")