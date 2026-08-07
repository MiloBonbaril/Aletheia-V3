# 💬 I/O Discord

This service is a Discord bot. It is the gateway between the Discord users and the cognitive core of
Nexus-V. It is written in **Python**, with `discord.py`.

## 🎯 Functions

- **Message input:** it listens to the messages of the configured guild and publishes them on
  `io.user.msg.text`. It puts the display name of the author in front of the text. It adds a maximum
  of 5 image attachments.
- **Answer output:** it subscribes to `lobe.fragment_stream` and sends the fragments to the Discord
  channel in real time.
- **Voice presence:** it publishes `io.presence.discord_voice` at each change of occupation of a
  voice channel. Other services use this signal to know if an audience is present.
- **Voice capture:** the `/voice join`, `/voice leave` and `/voice record` commands. The record
  command captures an mp3 sample of all the speakers. Use it to verify the capture.
- **Real-time voice streaming:** after a successful `/voice join`, the service publishes the PCM
  audio of each speaker on `io.discord.voice.frame`, with the identity of the speaker. Start
  `io_oreilles` with `--discord` to transcribe it. The `/voice record` command stops this stream
  during the manual capture, then starts it again.
- **Real-time voice playback:** it subscribes to `io.voice.speak.audio` and plays each synthesized
  fragment in the voice channel that it joined. It does not use the local loudspeaker of `io_voix`.
  If the bot is in no channel, it ignores the audio.
- **Bets:** the `/bets` command group, for the bets between the members of the guild. This function
  is independent of the AI. It keeps its data in `data/bets.json`.

## 🎛️ Cogs

The `COGS` list in `bot.py` selects the active cogs.

| Cog | Function |
|---|---|
| `text` | Message input and answer output. The `/text activate_chat` command starts and stops the chat. |
| `presence` | The voice presence signal. |
| `voice` | The `/voice` command group, the audio capture and the audio playback. |
| `bets` | The `/bets` command group, and the `/bets manage` subgroup for the administrators. |
| `special_message` | Automatic answers to some French expressions. Not active. |

The `/reloadcogs` and `/reloadcog <name>` commands load the cogs again during operation.

## ⚙️ Configuration and start

### Prerequisites

- Python 3.12 or higher.
- `ffmpeg` on the PATH. The `/voice record` command uses it to make the mp3 file. The voice playback
  uses it to decode and resample the audio from `io_voix`.
- A NATS server on `localhost:4222`.

```bash
pip install -r requirements.txt
python bot.py
```

The NATS address is in the source code of each cog. The service does not read `NATS_URL`.

### Environment variables (`.env`)

| Variable | Function |
|---|---|
| `DISCORD_TOKEN` | The authentication token of the bot. |
| `DISCORD_GUILD_ID` | The guild that the bot listens to. The slash commands are specific to this guild. |
| `DISCORD_USER_ID` | The user that receives the notifications. |
| `TEXT_CHANNEL_ID` | The text channel that the bot uses. |
| `COMMAND_PREFIX` | The prefix of the classic commands. The default is `!`. |

### Voice encryption (DAVE)

Discord closes the voice connection with the code 4017 if the client does not support DAVE
end-to-end encryption. Thus the versions in `requirements.txt` are necessary:

- `discord.py[voice]==2.7.1`
- `davey==0.1.6`
- `discord-ext-voice-recv` from the head of the unmerged upstream pull request
  [#54](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/54). Remove the pin when a
  PyPI release contains it.

The bot writes the voice diagnostic records to `discord_voice.log`. This file is how the 4017 failure
was found. Read it first if a voice connection fails.

## 🧪 Tests

```bash
pytest tests/
```

The tests cover the voice cog, the audio bridge and the presence cog.

## 🔌 NATS interface

- **Publishes on:** `io.user.msg.text`, `io.presence.discord_voice`, `io.discord.voice.frame`
- **Subscribes to:** `lobe.fragment_stream`, `io.voice.speak.audio`
