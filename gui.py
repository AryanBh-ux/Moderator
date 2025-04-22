import discord
from discord import ui
from typing import List, Dict, Optional, Set, Any, Tuple
from database import get_roles_data, save_roles_data, get_swear_data, save_swear_data, load_logging_channel,save_logging_channel
from swear_filter import SwearFilter, split_words
import asyncio
from shared import guild_filters 

# Constants for UI consistency
DEFAULT_TIMEOUT = 300  # 5 minutes
EPHEMERAL_DELAY = 5    # seconds
ITEMS_PER_PAGE = {
    'words': 10,
    'selection': 25
}

# Color palette
COLORS = {
    'primary': 0x5865F2,
    'success': 0x2ECC71,
    'danger': 0xE74C3C,
    'warning': 0xF39C12,
    'info': 0x3498DB,
    'neutral': 0x95A5A6
}

class GuildState:
    """Maintains state for each guild using the bot."""
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.current_message: Optional[discord.Message] = None
        self.swear_filter: Optional[SwearFilter] = None
        self.ephemeral_messages: List[discord.Message] = []

    def get_filter(self) -> SwearFilter:
        """Lazy-load the swear filter for this guild."""
        if not self.swear_filter:
            swear_data = get_swear_data(self.guild_id)
            self.swear_filter = SwearFilter(swear_data["swear_words"])
        return self.swear_filter
    
    def refresh_filter(self) -> None:
        """Refresh the swear filter with latest data from the database."""
        swear_data = get_swear_data(self.guild_id)
        self.swear_filter = SwearFilter(swear_data["swear_words"])
        
    async def cleanup_ephemeral(self):
        """Clean up ephemeral messages after a delay."""
        await asyncio.sleep(EPHEMERAL_DELAY)
        messages_to_remove = self.ephemeral_messages.copy()
        self.ephemeral_messages = []  # Clear the list first to avoid accumulation
        
        for msg in messages_to_remove:
            try:
                await msg.delete()
            except (discord.NotFound, discord.HTTPException) as e:
                print(f"Error deleting ephemeral message: {e}")


class SwearGuardGUI:
    """Main GUI system for the swear filter management."""
    def __init__(self, bot):
        self.bot = bot
        self.guild_states: Dict[int, GuildState] = {}

    def get_guild_state(self, guild_id: int) -> GuildState:
        """Get or create a GuildState for the given guild."""
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = GuildState(guild_id)
        return self.guild_states[guild_id]

    async def update_message(self, interaction: discord.Interaction, 
                           embed: discord.Embed, view: ui.View) -> Optional[discord.Message]:
        """
        Helper to update existing message or send new one.
        Returns the message that was sent/updated.
        """
        guild_state = self.get_guild_state(interaction.guild.id)
        
        try:
            if guild_state.current_message:
                await guild_state.current_message.edit(embed=embed, view=view)
                return guild_state.current_message
            
            if interaction.response.is_done():
                guild_state.current_message = await interaction.followup.send(
                    embed=embed, view=view, ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    embed=embed, view=view, ephemeral=True
                )
                guild_state.current_message = await interaction.original_response()
            return guild_state.current_message
            
        except Exception as e:
            print(f"Error updating message: {e}")
            # Fallback to sending new message
            try:
                if interaction.response.is_done():
                    guild_state.current_message = await interaction.followup.send(
                        embed=embed, view=view, ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        embed=embed, view=view, ephemeral=True
                    )
                    guild_state.current_message = await interaction.original_response()
                return guild_state.current_message
            except Exception as e:
                print(f"Failed to send fallback message: {e}")
                return None

    async def create_dashboard(self, interaction: discord.Interaction):
        """Create and display the main dashboard."""
        guild_state = self.get_guild_state(interaction.guild.id)
        guild_state.current_message = None  # Reset message tracking
        
        view = DashboardView(interaction.guild, self)
        embed = discord.Embed(
            title="🛡️ SwearFilter Dashboard",
            description="Manage all filter settings from one place",
            color=COLORS['primary']
        )
        
        # Add quick stats
        swear_data = get_swear_data(interaction.guild.id)
        roles_data = get_roles_data(interaction.guild.id)
        
        embed.add_field(
            name="📊 Stats",
            value=f"• Filtered Words: {len(swear_data['swear_words'])}\n"
                 f"• Allowed Channels: {len(swear_data['allowed_channels'])}\n"
                 f"• Immune Roles: {len(roles_data['immune_roles'])}",
            inline=False
        )
        
        embed.add_field(
            name="⚡ Quick Actions",
            value="Click the buttons below to manage different aspects of the filter system",
            inline=False
        )
        
        await self.update_message(interaction, embed, view)

class BaseView(ui.View):
    """Base view with common functionality."""
    def __init__(self, guild: discord.Guild, gui_system: SwearGuardGUI, timeout: float = DEFAULT_TIMEOUT):
        super().__init__(timeout=timeout)
        self.guild = guild
        self.gui_system = gui_system

    async def _send_ephemeral(self, interaction: discord.Interaction, content: str) -> bool:
        """Send ephemeral message that auto-deletes. Returns success status."""
        try:
            if interaction.response.is_done():
                msg = await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
                msg = await interaction.original_response()
            
            guild_state = self.gui_system.get_guild_state(self.guild.id)
            guild_state.ephemeral_messages.append(msg)
            # Schedule cleanup task instead of calling directly
            asyncio.create_task(guild_state.cleanup_ephemeral())
            return True
        except Exception as e:
            print(f"Error sending ephemeral message: {e}")
            return False
        
    async def on_timeout(self) -> None:
        """Disable all buttons on timeout."""
        for item in self.children:
            if hasattr(item, 'disabled'):
                item.disabled = True

class DashboardView(BaseView):
    """Main dashboard view with navigation options."""
    def __init__(self, guild: discord.Guild, gui_system: SwearGuardGUI):
        super().__init__(guild, gui_system)
        self._setup_buttons()
        self.embed = self._create_embed()

    def _setup_buttons(self) -> None:
        """Set up the navigation buttons."""
        actions = [
            ("Role Manager", "🛡️", self._role_manager, discord.ButtonStyle.blurple),
            ("Word Manager", "📜", self._word_manager, discord.ButtonStyle.green),
            ("Channel Settings", "🔊", self._channel_settings, discord.ButtonStyle.gray),
            ("Test Filter", "🧪", self._test_filter, discord.ButtonStyle.secondary),
            ("Help Guide", "❔", self._show_help, discord.ButtonStyle.red)
        ]
        
        for label, emoji, callback, style in actions:
            btn = ui.Button(label=label, emoji=emoji, style=style)
            btn.callback = callback
            self.add_item(btn)

    def _create_embed(self) -> discord.Embed:
        """Create the dashboard embed."""
        embed = discord.Embed(
            title="🛡️ SwearFilter Dashboard",
            description="Manage all filter settings from one place",
            color=COLORS['primary']
        )
        
        swear_data = get_swear_data(self.guild.id)
        roles_data = get_roles_data(self.guild.id)
        
        embed.add_field(
            name="📊 Stats",
            value=f"• Filtered Words: {len(swear_data['swear_words'])}\n"
                 f"• Allowed Channels: {len(swear_data['allowed_channels'])}\n"
                 f"• Immune Roles: {len(roles_data['immune_roles'])}",
            inline=False
        )
        
        embed.set_footer(text="Settings are applied in real-time")
        return embed

    async def _role_manager(self, interaction: discord.Interaction) -> None:
        """Handle role manager button click."""
        await interaction.response.defer(ephemeral=True)
        view = RoleManagerView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)

    async def _word_manager(self, interaction: discord.Interaction) -> None:
        """Handle word manager button click."""
        await interaction.response.defer(ephemeral=True)
        view = WordManagerView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)

    async def _channel_settings(self, interaction: discord.Interaction) -> None:
        """Handle channel settings button click."""
        await interaction.response.defer(ephemeral=True)
        view = ChannelSettingsView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)

    async def _test_filter(self, interaction: discord.Interaction) -> None:
        """Handle test filter button click."""
        guild_state = self.gui_system.get_guild_state(self.guild.id)
        guild_state.refresh_filter()
        
        modal = TestModal(guild_state)
        await interaction.response.send_modal(modal)

    async def _show_help(self, interaction: discord.Interaction) -> None:
        """Handle help button click."""
        await interaction.response.defer(ephemeral=True)
        view = HelpView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)

class WordManagerView(BaseView):
    """View for managing filtered words."""
    def __init__(self, guild: discord.Guild, gui_system: SwearGuardGUI):
        super().__init__(guild, gui_system)
        self.swear_data = get_swear_data(guild.id)
        self.current_page = 0
        self.search_term = None
        self._setup_ui()
        self.embed = self._create_embed()

    def _setup_ui(self) -> None:
        """Set up the UI components."""
        # Search button
        search_btn = ui.Button(
            label="Search",
            emoji="🔍",
            style=discord.ButtonStyle.secondary
        )
        search_btn.callback = self._search_words
        self.add_item(search_btn)

        # Clear search button
        clear_btn = ui.Button(
            label="Clear Search",
            emoji="🧹",
            style=discord.ButtonStyle.secondary
        )
        clear_btn.callback = self._clear_search
        self.add_item(clear_btn)

        # Navigation buttons
        nav_buttons = [
            ("Add Words", "➕", self._add_words, discord.ButtonStyle.success),
            ("Remove Options", "➖", self._show_remove_options, discord.ButtonStyle.danger),
            ("Previous", "⬅️", self._prev_page, discord.ButtonStyle.secondary),
            ("Next", "➡️", self._next_page, discord.ButtonStyle.secondary),
            ("Back", "↩️", self._go_back, discord.ButtonStyle.red)
        ]
        
        for label, emoji, callback, style in nav_buttons:
            btn = ui.Button(label=label, emoji=emoji, style=style)
            btn.callback = callback
            self.add_item(btn)

    def _create_embed(self) -> discord.Embed:
        """Create the word management embed."""
        words = self.swear_data["swear_words"]
        
        # Apply search filter if exists
        if self.search_term:
            words = [w for w in words if self.search_term.lower() in w.lower()]
        
        total_words = len(words)
        total_pages = max(1, (total_words + ITEMS_PER_PAGE['words'] - 1) // ITEMS_PER_PAGE['words'])
        
        embed = discord.Embed(
            title="📜 Word Management",
            description=f"Page {self.current_page + 1}/{total_pages}",
            color=COLORS['success']
        )
        
        if self.search_term:
            embed.description = f"Search: '{self.search_term}' | {embed.description}"
        
        start_idx = self.current_page * ITEMS_PER_PAGE['words']
        end_idx = start_idx + ITEMS_PER_PAGE['words']
        page_words = words[start_idx:end_idx]
        
        if page_words:
            embed.add_field(
                name=f"Filtered Words ({total_words} total)",
                value="\n".join(f"• {word}" for word in page_words),
                inline=False
            )
        else:
            embed.add_field(
                name="No Words Found" if self.search_term else "No Words",
                value="Try a different search term" if self.search_term else "Add words using the button below",
                inline=False
            )
            
        return embed

    async def _search_words(self, interaction: discord.Interaction) -> None:
        """Handle search words button click."""
        modal = SearchModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        
        if modal.search_term.value:
            self.search_term = modal.search_term.value.strip()
            self.current_page = 0  # Reset to first page when searching
            self.embed = self._create_embed()
            await self.gui_system.update_message(interaction, self.embed, self)

    async def _clear_search(self, interaction: discord.Interaction) -> None:
        """Handle clear search button click."""
        if self.search_term:
            self.search_term = None
            self.current_page = 0
            self.embed = self._create_embed()
            await interaction.response.edit_message(embed=self.embed, view=self)
        else:
            await self._send_ephemeral(interaction, "No active search to clear")

    async def _add_words(self, interaction: discord.Interaction) -> None:
        """Handle add words button click."""
        modal = AddWordsModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        
        if modal.words.value:
            new_words = split_words(modal.words.value)
            self.swear_data = get_swear_data(self.guild.id)  # Refresh data
            existing_words = set(self.swear_data["swear_words"])
            words_to_add = [w for w in new_words if w not in existing_words]
            
            if not words_to_add:
                await self._send_ephemeral(interaction, "All words already in filter")
                return
                
            self.swear_data["swear_words"].extend(words_to_add)
            save_swear_data(self.guild.id, self.swear_data)
            
            # Update both GUI and main filter
            guild_state = self.gui_system.get_guild_state(self.guild.id)
            guild_state.refresh_filter()
            if self.guild.id in guild_filters:  # Update main filter if exists
                guild_filters[self.guild.id] = SwearFilter(self.swear_data["swear_words"])
            
            self.embed = self._create_embed()
            await self.gui_system.update_message(interaction, self.embed, self)
            await self._send_ephemeral(interaction, f"Added {len(words_to_add)} words to filter")

    async def _show_remove_options(self, interaction: discord.Interaction) -> None:
        """Handle remove options button click."""
        await interaction.response.defer()
        view = RemoveOptionsView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)

    async def _prev_page(self, interaction: discord.Interaction) -> None:
        """Handle previous page button click."""
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
            self.embed = self._create_embed()
            await self.gui_system.update_message(interaction, self.embed, self)

    async def _next_page(self, interaction: discord.Interaction) -> None:
        """Handle next page button click."""
        await interaction.response.defer()
        words = self.swear_data["swear_words"]
        if self.search_term:
            words = [w for w in words if self.search_term.lower() in w.lower()]
        max_page = (len(words) + ITEMS_PER_PAGE['words'] - 1) // ITEMS_PER_PAGE['words'] - 1
        if self.current_page < max_page:
            self.current_page += 1
            self.embed = self._create_embed()
            await self.gui_system.update_message(interaction, self.embed, self)

    async def _go_back(self, interaction: discord.Interaction) -> None:
        """Handle back button click."""
        await interaction.response.defer()
        view = DashboardView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)

class SearchModal(ui.Modal, title="Search Words"):
    search_term = ui.TextInput(
        label="Search term",
        placeholder="Enter part of a word to search for...",
        style=discord.TextStyle.short,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

class AddWordsModal(ui.Modal, title="Add Words to Filter"):
    words = ui.TextInput(
        label="Words to add",
        placeholder="Separate with spaces or commas",
        style=discord.TextStyle.paragraph,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

class RemoveOptionsView(BaseView):
    """View for choosing how to remove words."""
    def __init__(self, guild: discord.Guild, gui_system: SwearGuardGUI):
        super().__init__(guild, gui_system, timeout=60)
        self.swear_data = get_swear_data(guild.id)
        self._setup_ui()
        self.embed = self._create_embed()

    def _setup_ui(self) -> None:
        """Set up the remove options UI."""
        # Option 1: Select from list
        select_btn = ui.Button(
            label="Select from List",
            style=discord.ButtonStyle.primary,
            emoji="📋"
        )
        select_btn.callback = self._select_from_list
        self.add_item(select_btn)

        # Option 2: Type manually
        manual_btn = ui.Button(
            label="Type Manually",
            style=discord.ButtonStyle.secondary,
            emoji="⌨️"
        )
        manual_btn.callback = self._type_manually
        self.add_item(manual_btn)

        # Back button
        back_btn = ui.Button(
            label="Back",
            style=discord.ButtonStyle.red,
            emoji="⬅️"
        )
        back_btn.callback = self._go_back
        self.add_item(back_btn)

    def _create_embed(self) -> discord.Embed:
        """Create the remove options embed."""
        embed = discord.Embed(
            title="Remove Words",
            description="Choose how you want to remove words from the filter",
            color=COLORS['danger']
        )
        embed.add_field(
            name="Options",
            value="• Select from List: Choose words from a dropdown\n"
                  "• Type Manually: Enter words separated by spaces or commas",
            inline=False
        )
        return embed

    async def _select_from_list(self, interaction: discord.Interaction) -> None:
        """Handle select from list button click."""
        if not self.swear_data["swear_words"]:
            await self._send_ephemeral(interaction, "No words to remove - the filter list is empty!")
            return
            
        await interaction.response.defer()
        view = WordSelectionView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)

    async def _type_manually(self, interaction: discord.Interaction) -> None:
        """Handle type manually button click."""
        if not self.swear_data["swear_words"]:
            await self._send_ephemeral(interaction, "No words to remove - the filter list is empty!")
            return
            
        modal = RemoveWordsModal(self.guild.id)
        await interaction.response.send_modal(modal)
        await modal.wait()
        
        if modal.words_to_remove:
            self.swear_data = get_swear_data(self.guild.id)
            self.swear_data["swear_words"] = [
                w for w in self.swear_data["swear_words"]
                if w not in modal.words_to_remove
            ]
            save_swear_data(self.guild.id, self.swear_data)
            
            # Update both GUI and main filter
            guild_state = self.gui_system.get_guild_state(self.guild.id)
            guild_state.refresh_filter()
            if self.guild.id in guild_filters:
                guild_filters[self.guild.id] = SwearFilter(self.swear_data["swear_words"])
            
            view = WordManagerView(self.guild, self.gui_system)
            await self.gui_system.update_message(interaction, view.embed, view)
            await self._send_ephemeral(interaction, f"Removed {len(modal.words_to_remove)} words")

    async def _go_back(self, interaction: discord.Interaction) -> None:
        """Handle back button click."""
        await interaction.response.defer()
        view = WordManagerView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)

class RemoveWordsModal(ui.Modal, title="Remove Words from Filter"):
    words = ui.TextInput(
        label="Words to remove",
        placeholder="Separate with spaces or commas",
        style=discord.TextStyle.paragraph,
        required=True
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id
        self.words_to_remove = []

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission."""
        swear_data = get_swear_data(self.guild_id)
        input_words = split_words(self.words.value)
        self.words_to_remove = [w for w in input_words if w in swear_data["swear_words"]]
        await interaction.response.defer()

class WordSelectionView(BaseView):
    """View for selecting words to remove from list."""
    def __init__(self, guild: discord.Guild, gui_system: SwearGuardGUI):
        super().__init__(guild, gui_system)
        self.swear_data = get_swear_data(guild.id)
        self.selected_words: List[str] = []
        self._setup_ui()
        self.embed = self._create_embed()

    def _setup_ui(self) -> None:
        """Set up the word selection UI."""
        word_select = ui.Select(
            placeholder="Select words to remove...",
            options=[
                discord.SelectOption(label=word, value=word)
                for word in self.swear_data["swear_words"]
            ],
            min_values=1,
            max_values=min(ITEMS_PER_PAGE['selection'], len(self.swear_data["swear_words"]))
        )
        word_select.callback = self._on_word_select
        self.add_item(word_select)
        
        remove_btn = ui.Button(
            label="Remove Selected",
            style=discord.ButtonStyle.danger,
            emoji="🗑️"
        )
        remove_btn.callback = self._remove_selected
        self.add_item(remove_btn)
        
        back_btn = ui.Button(
            label="Back",
            style=discord.ButtonStyle.secondary,
            emoji="⬅️"
        )
        back_btn.callback = self._go_back
        self.add_item(back_btn)

    def _create_embed(self) -> discord.Embed:
        """Create the word selection embed."""
        embed = discord.Embed(
            title="Remove Words",
            description="Select words to remove from the filter",
            color=COLORS['danger']
        )
        
        if self.selected_words:
            embed.add_field(
                name="Selected for Removal",
                value="\n".join(f"• {word}" for word in self.selected_words),
                inline=False
            )
        
        return embed

    async def _on_word_select(self, interaction: discord.Interaction) -> None:
        """Handle word selection."""
        self.selected_words = interaction.data["values"]
        self.embed = self._create_embed()
        await interaction.response.edit_message(embed=self.embed)

    async def _remove_selected(self, interaction: discord.Interaction) -> None:
        """Handle remove selected button click."""
        if not self.selected_words:
            await self._send_ephemeral(interaction, "Please select words first!")
            return
        
        await interaction.response.defer()
        
        self.swear_data = get_swear_data(self.guild.id)
        self.swear_data["swear_words"] = [
            w for w in self.swear_data["swear_words"]
            if w not in self.selected_words
        ]
        save_swear_data(self.guild.id, self.swear_data)
        
        # Update both GUI and main filter
        guild_state = self.gui_system.get_guild_state(self.guild.id)
        guild_state.refresh_filter()
        if self.guild.id in guild_filters:
            guild_filters[self.guild.id] = SwearFilter(self.swear_data["swear_words"])
        
        view = WordManagerView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)
        await self._send_ephemeral(interaction, f"Removed {len(self.selected_words)} words")

    async def _go_back(self, interaction: discord.Interaction) -> None:
        """Handle back button click."""
        await interaction.response.defer()
        view = RemoveOptionsView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)

class RoleManagerView(BaseView):
    """View for managing roles with special permissions."""
    def __init__(self, guild: discord.Guild, gui_system: SwearGuardGUI):
        super().__init__(guild, gui_system)
        self.roles_data = get_roles_data(guild.id)
        self.selected_role: Optional[discord.Role] = None
        self._setup_ui()
        self.embed = self._create_embed()

    def _setup_ui(self) -> None:
        """Set up the role management UI."""
        role_select = ui.RoleSelect(
            placeholder="Select a role...",
            max_values=1
        )
        role_select.callback = self._on_role_select
        self.add_item(role_select)

        actions = [
            ("Add Allowed", "✅", self._add_allowed, discord.ButtonStyle.success),
            ("Add Immune", "🛡️", self._add_immune, discord.ButtonStyle.primary),
            ("Remove", "🗑️", self._remove_role, discord.ButtonStyle.danger),
            ("Back", "⬅️", self._go_back, discord.ButtonStyle.secondary)
        ]
        
        for label, emoji, callback, style in actions:
            btn = ui.Button(label=label, emoji=emoji, style=style)
            btn.callback = callback
            self.add_item(btn)

    def _create_embed(self) -> discord.Embed:
        """Create the role management embed."""
        embed = discord.Embed(title="Role Management", color=COLORS['primary'])
        
        allowed_roles = "\n".join(
            f"• {role_name}" 
            for role_name in self.roles_data["allowed_roles"]
        ) or "None"
        
        immune_roles = "\n".join(
            f"• {role_name}" 
            for role_name in self.roles_data["immune_roles"]
        ) or "None"
        
        embed.add_field(
            name="🛡️ Allowed Roles (Can manage bot)",
            value=allowed_roles,
            inline=False
        )
        embed.add_field(
            name="✨ Immune Roles (Bypass filter)",
            value=immune_roles,
            inline=False
        )
        
        if self.selected_role:
            embed.set_footer(text=f"Selected: {self.selected_role.name}")
        
        return embed

    async def _on_role_select(self, interaction: discord.Interaction) -> None:
        """Handle role selection."""
        self.selected_role = interaction.guild.get_role(int(interaction.data["values"][0]))
        self.embed = self._create_embed()
        await interaction.response.edit_message(embed=self.embed)

    async def _add_allowed(self, interaction: discord.Interaction) -> None:
        """Handle add to allowed roles button click."""
        if not self.selected_role:
            return await self._send_ephemeral(interaction, "Please select a role first!")
        
        if self.selected_role.name not in self.roles_data["allowed_roles"]:
            self.roles_data["allowed_roles"].append(self.selected_role.name)
            save_roles_data(self.guild.id, self.roles_data)
        
        self.embed = self._create_embed()
        await interaction.response.edit_message(embed=self.embed)
        await self._send_ephemeral(interaction, f"Added {self.selected_role.name} to allowed roles")

    async def _add_immune(self, interaction: discord.Interaction) -> None:
        """Handle add to immune roles button click."""
        if not self.selected_role:
            return await self._send_ephemeral(interaction, "Please select a role first!")
        
        if self.selected_role.name not in self.roles_data["immune_roles"]:
            self.roles_data["immune_roles"].append(self.selected_role.name)
            save_roles_data(self.guild.id, self.roles_data)
        
        self.embed = self._create_embed()
        await interaction.response.edit_message(embed=self.embed)
        await self._send_ephemeral(interaction, f"Added {self.selected_role.name} to immune roles")

    async def _remove_role(self, interaction: discord.Interaction) -> None:
        """Handle remove role permissions button click."""
        if not self.selected_role:
            return await self._send_ephemeral(interaction, "Please select a role first!")
        
        removed = False
        if self.selected_role.name in self.roles_data["allowed_roles"]:
            self.roles_data["allowed_roles"].remove(self.selected_role.name)
            removed = True
        if self.selected_role.name in self.roles_data["immune_roles"]:
            self.roles_data["immune_roles"].remove(self.selected_role.name)
            removed = True
        
        if removed:
            save_roles_data(self.guild.id, self.roles_data)
            self.embed = self._create_embed()
            await interaction.response.edit_message(embed=self.embed)
            await self._send_ephemeral(interaction, f"Removed permissions from {self.selected_role.name}")
        else:
            await self._send_ephemeral(interaction, f"{self.selected_role.name} has no special permissions")

    async def _go_back(self, interaction: discord.Interaction) -> None:
        """Handle back button click."""
        await interaction.response.defer()
        view = DashboardView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)

# Update ChannelSettingsView in gui.py

class ChannelSettingsView(BaseView):
    """View for managing channel whitelist and logging settings."""
    def __init__(self, guild: discord.Guild, gui_system: SwearGuardGUI):
        super().__init__(guild, gui_system)
        self.swear_data = get_swear_data(guild.id)
        self.selected_channels: List[int] = []
        self._setup_ui()
        self.embed = self._create_embed()

    def _setup_ui(self) -> None:
        """Set up the channel settings UI."""
        # Channel selection dropdown
        channel_select = ui.ChannelSelect(
            placeholder="Select channels...",
            channel_types=[discord.ChannelType.text],
            max_values=25
        )
        channel_select.callback = self._on_channel_select
        self.add_item(channel_select)

        # Toggle button for whitelist
        toggle_btn = ui.Button(
            label="Toggle Allow/Block",
            style=discord.ButtonStyle.primary,
            emoji="🔀"
        )
        toggle_btn.callback = self._toggle_channels
        self.add_item(toggle_btn)

        # Button for setting logging channel
        logging_btn = ui.Button(
            label="Set Logging Channel",
            style=discord.ButtonStyle.secondary,
            emoji="📝"
        )
        logging_btn.callback = self._set_logging_channel
        self.add_item(logging_btn)

        # Back button
        back_btn = ui.Button(
            label="Back",
            style=discord.ButtonStyle.secondary,
            emoji="⬅️"
        )
        back_btn.callback = self._go_back
        self.add_item(back_btn)

    def _create_embed(self) -> discord.Embed:
        """Create the channel settings embed."""
        embed = discord.Embed(
            title="🔊 Channel Settings",
            description="Manage where swearing is allowed and logging",
            color=COLORS['neutral']
        )
        
        # Allowed channels section
        allowed_channels = []
        for channel_id in self.swear_data["allowed_channels"]:
            channel = self.guild.get_channel(channel_id)
            if channel:
                allowed_channels.append(f"• {channel.mention}")
        
        embed.add_field(
            name="Allowed Channels",
            value="\n".join(allowed_channels) or "None",
            inline=False
        )
        
        # Logging channel section
        logging_channel_id = load_logging_channel(self.guild.id)
        logging_channel = self.guild.get_channel(logging_channel_id) if logging_channel_id else None
        
        embed.add_field(
            name="Logging Channel",
            value=logging_channel.mention if logging_channel else "Not set",
            inline=False
        )
        
        if self.selected_channels:
            embed.set_footer(text=f"{len(self.selected_channels)} channels selected")
        
        return embed

    async def _on_channel_select(self, interaction: discord.Interaction) -> None:
        """Handle channel selection."""
        self.selected_channels = [
            int(channel_id) for channel_id in interaction.data["values"]
        ]
        self.embed = self._create_embed()
        await interaction.response.edit_message(embed=self.embed)

    async def _toggle_channels(self, interaction: discord.Interaction) -> None:
        """Handle toggle channels button click."""
        if not self.selected_channels:
            return await self._send_ephemeral(interaction, "Please select channels first!")
        
        changes = 0
        for channel_id in self.selected_channels:
            if channel_id in self.swear_data["allowed_channels"]:
                self.swear_data["allowed_channels"].remove(channel_id)
                changes -= 1
            else:
                self.swear_data["allowed_channels"].append(channel_id)
                changes += 1
        
        if changes != 0:
            save_swear_data(self.guild.id, self.swear_data)
            self.embed = self._create_embed()
            await interaction.response.edit_message(embed=self.embed)
            action = "Allowed" if changes > 0 else "Blocked"
            await self._send_ephemeral(interaction, f"{action} {abs(changes)} channels")
        else:
            await self._send_ephemeral(interaction, "No changes made - channels already in desired state")

    async def _set_logging_channel(self, interaction: discord.Interaction) -> None:
        """Handle setting logging channel button click."""
        if not self.selected_channels:
            return await self._send_ephemeral(interaction, "Please select a channel first!")
        
        if len(self.selected_channels) > 1:
            return await self._send_ephemeral(interaction, "Please select only one channel for logging!")
        
        channel_id = self.selected_channels[0]
        if save_logging_channel(self.guild.id, channel_id):
            self.embed = self._create_embed()
            await interaction.response.edit_message(embed=self.embed, view=self)
            await self._send_ephemeral(interaction, f"✅ Set logging channel to <#{channel_id}>")
        else:
            await self._send_ephemeral(interaction, "❌ Failed to set logging channel")

    async def _go_back(self, interaction: discord.Interaction) -> None:
        """Handle back button click."""
        await interaction.response.defer()
        view = DashboardView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)

class TestModal(ui.Modal, title="Test Filter"):
    def __init__(self, guild_state: GuildState):
        super().__init__()
        self.guild_state = guild_state
        self.message_input = ui.TextInput(
            label="Message to test",
            placeholder="Type something to check against the filter...",
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.message_input)

    # In the TestModal class in gui.py, modify the on_submit method:
    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission."""
        # Use the guild's filter instance
        filter_instance = self.guild_state.get_filter()
        result = await filter_instance.contains_swear_word(self.message_input.value)
        
        embed = discord.Embed(
            title="🧪 Test Results",
            color=COLORS['warning'] if result else COLORS['success']
        )
        
        embed.add_field(
            name="Message",
            value=f"```{self.message_input.value}```",
            inline=False
        )
        
        embed.add_field(
            name="Result",
            value="❌ Would be **BLOCKED**" if result else "✅ Would be **ALLOWED**",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
class HelpView(BaseView):
    """View for displaying help information."""
    def __init__(self, guild: discord.Guild, gui_system: SwearGuardGUI):
        super().__init__(guild, gui_system)
        self.current_page = 0
        self._setup_buttons()
        self.embed = self._create_embed()

    def _setup_buttons(self) -> None:
        """Set up the help navigation buttons."""
        pages = ["Welcome", "Roles", "Words", "Channels", "Features"]
        
        for i, page in enumerate(pages):
            btn = ui.Button(
                label=page,
                style=discord.ButtonStyle.primary if i == 0 else discord.ButtonStyle.secondary,
                custom_id=f"page_{i}"
            )
            btn.callback = self._change_page
            self.add_item(btn)
        
        back_btn = ui.Button(
            label="Back",
            style=discord.ButtonStyle.red,
            emoji="⬅️"
        )
        back_btn.callback = self._go_back
        self.add_item(back_btn)

    def _create_embed(self) -> discord.Embed:
        """Create the help embed for the current page."""
        pages = [
            self._create_welcome_page(),
            self._create_roles_help(),
            self._create_words_help(),
            self._create_channels_help(),
            self._create_features_help()
        ]
        return pages[self.current_page]

    def _create_welcome_page(self) -> discord.Embed:
        """Create the welcome help page."""
        embed = discord.Embed(
            title="🛡️ SwearFilter Bot Help",
            description="Comprehensive guide to using the SwearFilter bot's features",
            color=COLORS['info']
        )
        embed.add_field(
            name="Getting Started",
            value="• Use the dashboard for visual management\n"
                 "• All changes take effect immediately\n"
                 "• Settings sync across all interfaces",
            inline=False
        )
        embed.add_field(
            name="Main Features",
            value="• Advanced swear word filtering\n"
                 "• Role-based permissions\n"
                 "• Channel-specific rules\n"
                 "• Coming soon: Spam detection, mass mention protection",
            inline=False
        )
        embed.set_footer(text="Click the buttons below for more information")
        return embed

    def _create_roles_help(self) -> discord.Embed:
        """Create the roles help page."""
        embed = discord.Embed(
            title="👮 Role Management Help",
            color=COLORS['info']
        )
        embed.add_field(
            name="Allowed Roles",
            value="These roles can configure the bot's settings\n"
                 "• Server owner always has full access\n"
                 "• Add management roles with `/addallowedrole` or via GUI",
            inline=False
        )
        embed.add_field(
            name="Immune Roles",
            value="Members with these roles bypass all filtering\n"
                 "• Useful for moderators and admins\n"
                 "• Add with `/addimmunerole` or via GUI",
            inline=False
        )
        return embed

    def _create_words_help(self) -> discord.Embed:
        """Create the words help page."""
        embed = discord.Embed(
            title="📜 Word Filtering Help",
            color=COLORS['info']
        )
        embed.add_field(
            name="Smart Detection",
            value="The filter catches:\n"
                 "• Common variations (h*ck, h3ck, h e c k)\n"
                 "• Repeated characters (heeelllooo)\n"
                 "• Characters in different variations such as Homoglyphs, Leetspeak and much more (sh𝖎t, sh¡t,shıt,shlt,sh1t,sh͟i͟t)\n"
                 "• Matches in longer words",
            inline=False
        )
        embed.add_field(
            name="Managing Words",
            value="• Add words with `/addswear` or via GUI\n"
                 "• Remove words with `/removeswear` or GUI\n"
                 "• View all filtered words with `/listswears`",
            inline=False
        )
        return embed

    def _create_channels_help(self) -> discord.Embed:
        """Create the channels help page."""
        embed = discord.Embed(
            title="🔊 Channel Settings Help",
            color=COLORS['info']
        )
        embed.add_field(
            name="Allowed Channels",
            value="Swearing is permitted in these channels\n"
                 "• The filter won't check messages here\n"
                 "• Set with `/setallowedswear` or via GUI",
            inline=False
        )
        embed.add_field(
            name="Best Practices",
            value="• Consider creating specific channels for relaxed rules\n"
                 "• Clearly label allowed channels in their topic\n"
                 "• Use role mentions to notify users of special channels",
            inline=False
        )
        return embed

    def _create_features_help(self) -> discord.Embed:
        """Create the features help page."""
        embed = discord.Embed(
            title="🛠️ Features & Roadmap",
            color=COLORS['info']
        )
        embed.add_field(
            name="Current Features",
            value="• Advanced swear word filtering\n"
                 "• Role-based permissions system\n"
                 "• Channel-specific rules\n"
                 "• Visual management dashboard\n"
                 "• Auto-moderation logs",
            inline=False
        )
        embed.add_field(
            name="Coming Soon",
            value="• Spam detection\n"
                 "• Mass mention protection\n"
                 "• Custom filter rules",
            inline=False
        )
        embed.set_footer(text="Suggest features with /feedback")
        return embed

    async def _change_page(self, interaction: discord.Interaction) -> None:
        """Handle help page navigation."""
        self.current_page = int(interaction.data["custom_id"].split("_")[1])
        self.embed = self._create_embed()
        await interaction.response.edit_message(embed=self.embed)

    async def _go_back(self, interaction: discord.Interaction) -> None:
        """Handle back button click."""
        await interaction.response.defer()
        view = DashboardView(self.guild, self.gui_system)
        await self.gui_system.update_message(interaction, view.embed, view)