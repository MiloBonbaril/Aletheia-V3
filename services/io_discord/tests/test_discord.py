import sys
import os
import tempfile
import json
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# Add service directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set dummy env variables for config initialization
os.environ["DISCORD_USER_ID"] = "987654321"
os.environ["TEXT_CHANNEL_ID"] = "555555"
os.environ["DISCORD_GUILD_ID"] = "123456789"
os.environ["DISCORD_TOKEN"] = "fake_token"


# Mock Discord library BEFORE importing cogs or bot
mock_discord = MagicMock()
mock_discord.Embed = MagicMock()
mock_discord.Color.blue.return_value = 0x0000ff
mock_discord.Color.green.return_value = 0x00ff00
mock_discord.Color.red.return_value = 0xff0000

class DummyCog:
    @staticmethod
    def listener(*args, **kwargs):
        return lambda func: func

class DummySlashCommandGroup:
    def __init__(self, *args, **kwargs):
        pass
    def create_subgroup(self, *args, **kwargs):
        return self
    def command(self, *args, **kwargs):
        return lambda func: func

mock_discord.SlashCommandGroup = DummySlashCommandGroup


mock_commands = MagicMock()
mock_commands.Cog = DummyCog
mock_commands.has_permissions = lambda *args, **kwargs: lambda func: func

mock_ext = MagicMock()
mock_ext.commands = mock_commands


sys.modules['discord'] = mock_discord
sys.modules['discord.ext'] = mock_ext
sys.modules['discord.ext.commands'] = mock_commands


# Mock config
import config
config.Config = MagicMock()
config.Config.GUILD_ID = 123456789
config.Config.DISCORD_TOKEN = "fake_token"
config.Config.USER_ID = 987654321
config.Config.TEXT_CHANNEL_ID = 555555
config.Config.COMMAND_PREFIX = "!"

from cogs.bets import BetsCog
from cogs.text import Text

@pytest.fixture
def temp_bets_file():
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as f:
        json.dump({}, f)
        temp_path = f.name
    yield temp_path
    if os.path.exists(temp_path):
        os.remove(temp_path)

def test_bets_cog_load_save(temp_bets_file):
    bot = MagicMock()
    # Patch the filepath logic in __init__
    with patch('cogs.bets.Path.mkdir'):
        cog = BetsCog(bot)
        cog.file_path = temp_bets_file
        
        # Initially empty
        bets = cog.load_bets()
        assert bets == {}
        
        # Set some data and save
        cog.bets = {"bet_1": {"title": "Match"}}
        cog.save_bets()
        
        # Reload
        loaded = cog.load_bets()
        assert loaded == {"bet_1": {"title": "Match"}}

@pytest.mark.asyncio
async def test_bets_cog_create_bet(temp_bets_file):
    bot = MagicMock()
    with patch('cogs.bets.Path.mkdir'):
        cog = BetsCog(bot)
        cog.file_path = temp_bets_file
        cog.bets = {}
        
        ctx = MagicMock()
        ctx.send = AsyncMock()
        title = "Championship"
        win_condition = "Win by 2 goals"
        participants = "<@11111> <@22222>"
        
        await cog.create_bet(ctx, title, win_condition, participants)
        
        # Check that a bet was created
        assert len(cog.bets) == 1
        bet_id = list(cog.bets.keys())[0]
        bet = cog.bets[bet_id]
        
        assert bet["title"] == title
        assert bet["win_condition"] == win_condition
        assert bet["participants"] == ["<@11111>", "<@22222>"]
        assert bet["bettors"] == {}
        assert not bet["resolved"]
        
        # Verify ctx.send was called (sending details/embed)
        ctx.send.assert_called_once()

@pytest.mark.asyncio
async def test_bets_cog_place_bet(temp_bets_file):
    bot = MagicMock()
    with patch('cogs.bets.Path.mkdir'):
        cog = BetsCog(bot)
        cog.file_path = temp_bets_file
        cog.bets = {
            "abc1": {
                "title": "Championship",
                "win_condition": "Win",
                "participants": ["<@11111>", "<@22222>"],
                "bettors": {},
                "resolved": False
            }
        }
        
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author = MagicMock()
        ctx.author.id = 999999
        ctx.author.mention = "<@999999>"
        
        # 1. Place a valid bet
        await cog.place_bet(ctx, "abc1", "<@11111>", 100)
        assert cog.bets["abc1"]["bettors"]["999999"] == {"participant": "<@11111>", "amount": 100}
        ctx.send.assert_called_with("<@999999> placed a **100** bet on **<@11111>** with ID `abc1`.")

        # 2. Place an invalid bet (invalid participant)
        ctx.send.reset_mock()
        await cog.place_bet(ctx, "abc1", "<@33333>", 100)
        ctx.send.assert_called_with("Invalid participant `<@33333>`. Choose from ['<@11111>', '<@22222>'].")

        # 3. Place an invalid bet (amount <= 0)
        ctx.send.reset_mock()
        await cog.place_bet(ctx, "abc1", "<@11111>", -50)
        ctx.send.assert_called_with("For monetary bets, please specify a positive amount.")

@pytest.mark.asyncio
async def test_bets_cog_declare_winner(temp_bets_file):
    bot = MagicMock()
    with patch('cogs.bets.Path.mkdir'):
        cog = BetsCog(bot)
        cog.file_path = temp_bets_file
        
        # Setup active bet with bettors
        # Total pool = 100 (user1) + 200 (user2) + 300 (user3) = 600
        # Winner = participant_A
        # Bettors on participant_A = user1 (100) + user3 (300) = 400
        # user1 share: (100 / 400) * 600 = 150
        # user3 share: (300 / 400) * 600 = 450
        cog.bets = {
            "abc1": {
                "title": "Championship",
                "win_condition": "Win",
                "participants": ["participant_A", "participant_B"],
                "bettors": {
                    "user1": {"participant": "participant_A", "amount": 100},
                    "user2": {"participant": "participant_B", "amount": 200},
                    "user3": {"participant": "participant_A", "amount": 300}
                },
                "resolved": False
            }
        }
        
        ctx = MagicMock()
        ctx.send = AsyncMock()
        await cog.declare_winner(ctx, "abc1", "participant_A")
        
        assert cog.bets["abc1"]["resolved"]
        assert cog.bets["abc1"]["winner"] == "participant_A"
        
        # Check embed generation
        ctx.send.assert_called_once()
        embed = ctx.send.call_args[1].get('embed')
        assert embed is not None
        
        # Verify the calculation details inside results field
        # The fields are stored inside embed
        results_field = next(f for f in embed.add_field.call_args_list if f[1].get('name') == 'Results')
        results_text = results_field[1].get('value')
        
        assert "user1" in results_text
        assert "150" in results_text
        assert "user3" in results_text
        assert "450" in results_text
