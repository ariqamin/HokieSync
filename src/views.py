from __future__ import annotations

import discord

from src.utils.formatters import format_udc_grade_rows, text_block


class UDCGradePageView(discord.ui.View):
    def __init__(
        self,
        owner_id: int,
        subject: str,
        course_number: str,
        rows: list[dict],
        page_size: int,
        instructor: str = "",
    ):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.subject = subject
        self.course_number = course_number
        self.rows = rows
        self.page_size = page_size
        self.instructor = instructor
        self.page = 0
        self.update_buttons()

    @property
    def page_count(self) -> int:
        return max(1, (len(self.rows) + self.page_size - 1) // self.page_size)

    def current_rows(self) -> list[dict]:
        start = self.page * self.page_size
        return self.rows[start : start + self.page_size]

    def message(self) -> str:
        return format_udc_grade_rows(
            self.subject,
            self.course_number,
            self.current_rows(),
            len(self.rows),
            self.instructor,
            page=self.page,
            page_size=self.page_size,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            text_block("UDC grades", ["Only the user who ran this lookup can turn these pages."]),
            ephemeral=True,
        )
        return False

    def update_buttons(self):
        self.previous_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.page_count - 1

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.update_buttons()
        await interaction.response.edit_message(content=self.message(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.page_count - 1, self.page + 1)
        self.update_buttons()
        await interaction.response.edit_message(content=self.message(), view=self)
