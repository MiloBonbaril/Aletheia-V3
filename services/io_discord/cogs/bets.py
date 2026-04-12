import discord
from discord.ext import commands
import json
import os
import re
import uuid
from pathlib import Path
from config import Config


class BetsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # The file that will store our bets data (relative to src/front)
        base_dir = Path(__file__).resolve().parent.parent  # src/front
        data_dir = base_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = str(data_dir / "bets.json")

        self.bets = self.load_bets()  # We load bets from the JSON file on startup

    bets = discord.SlashCommandGroup("bets", "Bet management commands", guild_ids=[Config.GUILD_ID])
    bets_management = bets.create_subgroup("manage", "Administrative bet management commands", guild_ids=[Config.GUILD_ID])

    # ---------------------------------------------------------------------
    # JSON DATA HANDLING
    # ---------------------------------------------------------------------
    def load_bets(self) -> dict:
        """
        Loads the bets dictionary from the JSON file.
        If the file doesn't exist, it creates an empty JSON file.
        """
        if not os.path.exists(self.file_path):
            with open(self.file_path, 'w') as f:
                json.dump({}, f, indent=4)

        with open(self.file_path, 'r') as f:
            return json.load(f)

    def save_bets(self) -> None:
        """
        Saves the current bets dictionary to the JSON file.
        """
        with open(self.file_path, 'w') as f:
            json.dump(self.bets, f, indent=4)

    # ---------------------------------------------------------------------
    # CREATING A BET
    # ---------------------------------------------------------------------
    @bets_management.command(guild_ids=[Config.GUILD_ID], name="create")
    @commands.has_permissions(administrator=True)
    async def create_bet(self, ctx, title: str, win_condition: str, participants:str):
        """
        Create a new bet with a single participants string.
        We expect mentions like: <@1234> <@5678>

        Usage:
          !createbet <title> <win_condition> <participants>

        Example:
          !createbet "Big Match" "First to 10 kills" "@User1 @User2"
        """
        # 1) Prepare to parse the participants string.
        # We'll split by whitespace and drop any empty entries:
        raw_parts = [p.strip() for p in participants.split(" ") if p.strip()]

        # 2) Validate each participant is a proper mention
        # Simple regex to match a mention pattern: <@...> or <@!...>
        mention_pattern = re.compile(r"^<@!?(\d+)>$")
        valid_participants = []

        for p in raw_parts:
            match = mention_pattern.match(p)
            if not match:
                return await ctx.send(
                    f"Invalid participant format: `{p}`. "
                    "Please mention users like `<@1234567890>`."
                )
            valid_participants.append(p)

        # 3) Generate a unique ID for the bet
        bet_id = str(uuid.uuid4())[:4]

        # 4) Create the structure for the new bet
        self.bets[bet_id] = {
            "title": title,
            "win_condition": win_condition,
            # We'll store the participant mentions as a list of strings
            "participants": valid_participants,
            "bettors": {},
            "resolved": False,
            "winner": None
        }

        self.save_bets()

        # 5) Send an embed with details
        embed = discord.Embed(title="New Bet Created!", color=discord.Color.green())
        embed.add_field(name="Bet ID", value=bet_id, inline=False)
        embed.add_field(name="Title", value=title, inline=False)
        embed.add_field(name="Win Condition", value=win_condition, inline=False)
        embed.add_field(
            name="Participants",
            value=", ".join(valid_participants),
            inline=False
        )
        embed.set_footer(
            text="Use !bet <bet_id> <participant_mention> [amount] to place your bet."
        )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------------------
    # PLACING A BET
    # ---------------------------------------------------------------------
    @bets.command(guild_ids=[Config.GUILD_ID], name="place_bet")
    async def place_bet(self, ctx, bet_id: str, participant: str, amount = 0):
        """
        Place a bet on a specific participant of a bet.

        Usage:
          !bet <bet_id> <participant> <bet_type> [amount]

        - bet_id: The unique ID of the bet
        - participant: Must match one in the bet's participants list
        - amount: If bet_type == "monetary", specify how much you're betting (integer)
        """
        # 1. Check if the bet_id is valid
        if bet_id not in self.bets:
            return await ctx.send(f"Bet with ID `{bet_id}` does not exist.")

        bet_data = self.bets[bet_id]

        # 2. Check if the bet is already resolved
        if bet_data.get("resolved"):
            return await ctx.send(f"This bet (`{bet_id}`) is already resolved. No new bets allowed.")

        # 3. Validate participant
        if participant not in bet_data["participants"]:
            return await ctx.send(
                f"Invalid participant `{participant}`. Choose from {bet_data['participants']}."
            )

        # 4. Validate bet type
        # if bet_type not in ["money", "else"]:
            # return await ctx.send("Bet type must be either 'money' or 'else'.")

        # 5. If bet type is monetary, ensure amount > 0
        if amount <= 0:
            return await ctx.send("For monetary bets, please specify a positive amount.")

        user_id = str(ctx.author.id)

        # Record the bet
        bet_data["bettors"][user_id] = {
            "participant": participant,
            #"type": bet_type,
            "amount": amount
        }

        self.save_bets()

        await ctx.send(f"{ctx.author.mention} placed a **{amount}** bet on **{participant}** with ID `{bet_id}`.")

    # ---------------------------------------------------------------------
    # MODIFY A BET
    # ---------------------------------------------------------------------
    @bets_management.command(guild_ids=[Config.GUILD_ID], name="modify_bet")
    @commands.has_permissions(administrator=True)
    async def modify_bet(self, ctx, bet_id: str, field: str, *, new_value: str):
        """
        Modify an existing bet. Only admins can do this.

        Usage:
          !modifybet <bet_id> <field> <new_value>

        - field can be one of: title, win_condition, participants
        - new_value is the updated value (for participants, pass them comma-separated).
        """
        if bet_id not in self.bets:
            return await ctx.send(f"Bet with ID `{bet_id}` does not exist.")

        bet_data = self.bets[bet_id]

        # Simple check to ensure we only allow certain fields
        if field not in ["title", "win_condition", "participants"]:
            return await ctx.send("You can only modify 'title', 'win_condition', or 'participants'.")

        if field == "participants":
            # new_value might be a string like "TeamA, TeamB, TeamC"
            participants_list = [p.strip() for p in new_value.split(",")]
            bet_data["participants"] = participants_list
        else:
            bet_data[field] = new_value

        self.save_bets()
        await ctx.send(f"Successfully modified `{field}` for bet `{bet_id}`.")

    # ---------------------------------------------------------------------
    # ADD A PARTICIPANT TO AN EXISTING BET
    # ---------------------------------------------------------------------
    @bets_management.command(guild_ids=[Config.GUILD_ID], name="add_participant")
    @commands.has_permissions(administrator=True)
    async def add_participant(self, ctx, bet_id: str, participant: str):
        """
        Add a single participant to an existing bet. Requires admin permissions.

        Usage:
          !addparticipant <bet_id> <participant_mention>
        """
        # 1) Check if bet exists
        if bet_id not in self.bets:
            return await ctx.send(f"Bet with ID `{bet_id}` does not exist.")

        # 2) Check if bet already resolved
        if self.bets[bet_id].get("resolved"):
            return await ctx.send(
                f"Bet `{bet_id}` is already resolved. Cannot add new participants."
            )

        # 3) Validate that 'participant' is a proper mention
        mention_pattern = re.compile(r"^<@!?(\d+)>$")
        match = mention_pattern.match(participant)
        if not match:
            return await ctx.send(
                f"Invalid participant format: `{participant}`. "
                "Please mention users like `<@1234567890>`."
            )

        # 4) Check if participant is already in the list
        if participant in self.bets[bet_id]["participants"]:
            return await ctx.send("That user is already a participant in this bet.")

        # 5) Append participant
        self.bets[bet_id]["participants"].append(participant)
        self.save_bets()

        await ctx.send(f"Participant {participant} added to bet `{bet_id}`.")

    # ---------------------------------------------------------------------
    # SHOW A SPECIFIC BET
    # ---------------------------------------------------------------------
    @bets.command(guild_ids=[Config.GUILD_ID], name="show_bet")
    async def show_bet(self, ctx, bet_id: str):
        """
        Display details of an existing bet.

        Usage:
          !showbet <bet_id>
        """
        if bet_id not in self.bets:
            return await ctx.send(f"Bet with ID `{bet_id}` does not exist.")

        bet_data = self.bets[bet_id]

        embed = discord.Embed(
            title=f"Bet ID: {bet_id}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Title", value=bet_data["title"], inline=False)
        embed.add_field(name="Win Condition", value=bet_data["win_condition"], inline=False)
        embed.add_field(
            name="Participants",
            value=", ".join(bet_data["participants"]) if bet_data["participants"] else "None",
            inline=False
        )

        # We'll show the basic status and winner if resolved
        if bet_data["resolved"]:
            winner_str = bet_data["winner"] if bet_data["winner"] else "No winner recorded."
            embed.add_field(name="Status", value="Resolved", inline=True)
            embed.add_field(name="Winner", value=winner_str, inline=True)
        else:
            embed.add_field(name="Status", value="Active (unresolved)", inline=True)

        # Optionally, show how many bettors are in
        embed.add_field(name="Total Bettors", value=str(len(bet_data["bettors"])), inline=True)

        await ctx.send(embed=embed)

    # ---------------------------------------------------------------------
    # DELETE A BET
    # ---------------------------------------------------------------------
    @bets_management.command(guild_ids=[Config.GUILD_ID], name="delete_bet")
    @commands.has_permissions(administrator=True)
    async def delete_bet(self, ctx, bet_id: str):
        """
        Delete an existing bet entirely (Admin only).

        Usage:
          !deletebet <bet_id>
        """
        if bet_id not in self.bets:
            return await ctx.send(f"Bet with ID `{bet_id}` does not exist.")

        del self.bets[bet_id]  # Remove it from the dictionary
        self.save_bets()

        await ctx.send(f"Bet with ID `{bet_id}` has been deleted.")

    # ---------------------------------------------------------------------
    # DECLARE A WINNER
    # ---------------------------------------------------------------------
    @bets_management.command(guild_ids=[Config.GUILD_ID], name="declare_winner")
    @commands.has_permissions(administrator=True)
    async def declare_winner(self, ctx, bet_id: str, winner: str):
        """
        Declare the winning participant of a bet (Admin only).

        Usage:
          !declarewinner <bet_id> <winner>

        - Sets the bet as resolved
        - Calculates the monetary payouts, if any
        - "something_else" bets are just listed
        """
        if bet_id not in self.bets:
            return await ctx.send(f"Bet with ID `{bet_id}` does not exist.")

        bet_data = self.bets[bet_id]

        # Check that the bet isn't already resolved
        if bet_data.get("resolved"):
            return await ctx.send(f"Bet `{bet_id}` was already resolved with winner `{bet_data.get('winner')}`.")

        # Validate winner
        if winner not in bet_data["participants"]:
            return await ctx.send(
                f"Invalid winner `{winner}`. Must be one of {bet_data['participants']}."
            )

        bet_data["resolved"] = True
        bet_data["winner"] = winner

        # Calculate payouts for monetary bets
        # 1. Sum the total monetary bets
        total_pool = sum(
            bettor_info["amount"]
            for bettor_info in bet_data["bettors"].values()
        )

        # 2. Sum the total bet on the winning participant
        total_on_winner = sum(
            bettor_info["amount"]
            for bettor_info in bet_data["bettors"].values()
            if bettor_info["participant"] == winner
        )

        # We'll construct a message to show who gets what
        embed = discord.Embed(title="Bet Resolved!", color=discord.Color.blue())
        embed.add_field(name="Bet ID", value=bet_id, inline=False)
        embed.add_field(name="Title", value=bet_data["title"], inline=False)
        embed.add_field(name="Winner", value=winner, inline=False)

        results_details = []
        # 3. For each bettor that bet on the winner, calculate their share
        for user_id, bettor_info in bet_data["bettors"].items():
            if bettor_info["participant"] == winner:
                bet_amount = bettor_info["amount"]
                if total_on_winner > 0:
                    # Weighted formula: (Amount bet / total bet on winner) * total_pool
                    share = (bet_amount / total_on_winner) * total_pool
                    results_details.append(
                        f"<@{user_id}> bet **{bet_amount}** and won **{round(share, 2)}**!"
                    )

        # If no one bet on the winner, mention that
        if not results_details:
            results_details.append("No one placed a winning bet.")

        embed.add_field(name="Results", value="\n".join(results_details), inline=False)

        self.save_bets()  # Save changes

        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(BetsCog(bot))
