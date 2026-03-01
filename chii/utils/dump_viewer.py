import pathlib

from discord import ButtonStyle, Interaction, ui


class DumpViewer(ui.View):
    def __init__(self, file_path: pathlib.Path, pages: list[str], owner_id: int) -> None:
        super().__init__(timeout=300)

        self.file_path = file_path
        self.pages = pages
        self.owner_id = owner_id

        self.index = 0

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user.id == self.owner_id

    def get_content(self) -> str:
        return (
            f"# {self.file_path.name}\n"
            f"```{self.file_path.suffix.replace('.', '')}\n"
            f"{self.pages[self.index]}\n"
            "```\n"
            f"-# Page **{self.index + 1}** of **{len(self.pages)}**"
        )

    @ui.button(label="Previous", style=ButtonStyle.secondary)
    async def previous_page(self, interaction: Interaction, _button: ui.Button) -> None:
        if self.index - 1 < 0:
            self.index = len(self.pages) - 1
        else:
            self.index -= 1

        await interaction.response.edit_message(content=self.get_content(), view=self)

    @ui.button(label="Next", style=ButtonStyle.secondary)
    async def next_page(self, interaction: Interaction, _button: ui.Button) -> None:
        if self.index + 1 > len(self.pages) - 1:
            self.index = 0
        else:
            self.index += 1

        await interaction.response.edit_message(content=self.get_content(), view=self)
